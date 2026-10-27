import hashlib
import importlib
import re
from threading import Event
from uuid import UUID, uuid4
from unittest import skipUnless

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from s015_0021_support import drop_unset_check, fresh
from tests.s015_concurrency_support import (
    cleanup_workers,
    exact_wait,
    lock_snapshot,
    start_worker,
    wait_until_locked,
)
from tests.sb2_route_support import birth, prepare_route
from tests.schema_witness_support import assert_trigger_delta, trigger_names


POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "register cleanup tests require PostgreSQL")


def _register_and_hold(organism_uuid, registered, release, *, reenter=False):
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])", [organism_uuid])
        registered.set()
        if not release.wait(timeout=30):
            raise TimeoutError("registration hold was not released")
        if reenter:
            cursor.execute("SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])", [organism_uuid])


def _cleanup_and_hold(cleanup_sql, cleaned, release, *, rollback=False):
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(cleanup_sql, params=None)
        cleaned.set()
        if not release.wait(timeout=30):
            raise TimeoutError("cleanup hold was not released")
        if rollback:
            transaction.set_rollback(True)


def _release_events(events):
    for release_event in events:
        release_event.set()


def _source_function_md5(cursor, source_sql):
    anchor = "CREATE OR REPLACE FUNCTION public.s015_register_organism_lock("
    temporary_sql = source_sql.replace(
        anchor,
        "CREATE FUNCTION pg_temp.s015_register_organism_lock(",
        1,
    )
    if temporary_sql == source_sql:
        raise AssertionError("register source did not contain the expected function declaration")
    cursor.execute(temporary_sql, params=None)
    try:
        cursor.execute(
            "SELECT pg_get_functiondef('pg_temp.s015_register_organism_lock(uuid)'::regprocedure)"
        )
        definition = re.sub(
            r"(?<=FUNCTION )pg_temp(?:_\d+)?\.",
            "public.",
            cursor.fetchone()[0],
            count=1,
        )
        return hashlib.md5(definition.encode("utf-8")).hexdigest()
    finally:
        cursor.execute("DROP FUNCTION pg_temp.s015_register_organism_lock(uuid)")


@POSTGRESQL_ONLY
class S015RegisterCleanupTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    reset_sequences = True

    def setUp(self):
        super().setUp()
        with connection.cursor() as cursor:
            drop_unset_check(cursor)

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE public.core_livingorganism "
                "ADD CONSTRAINT s015_platform_root_unset_ck CHECK (NOT platform_root) NOT VALID"
            )
        super().tearDown()

    def _organism_pair(self, label):
        first = prepare_route(tag=f"{label}-first")
        birth(first)
        second = prepare_route(tag=f"{label}-second")
        birth(second)
        return {
            "first": {"id": first.organism.pk, "uuid": first.organism.organism_id},
            "second": {"id": second.organism.pk, "uuid": second.organism.organism_id},
        }

    def _clear_stale_rows(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM public.s015_transaction_register WHERE kind = 'ORGANISM_LOCK' "
                "AND pg_xact_status(xid) IS DISTINCT FROM 'in progress'",
                params=None,
            )

    def _seed_stale_row(self, organism_uuid):
        self._clear_stale_rows()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [organism_uuid],
            )

    def _assert_registered_row_count(self, organism_uuid, expected):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.s015_transaction_register "
                "WHERE kind = 'ORGANISM_LOCK' AND organism_uuid = %s::uuid "
                "AND xid = pg_current_xact_id()",
                [organism_uuid],
            )
            self.assertEqual(cursor.fetchone()[0], expected)

    def _exact_organism_wait(self, waiter, blocker):
        with connection.cursor() as cursor:
            wait_until_locked(
                cursor,
                waiter.pid(),
                blocker.pid(),
                relations={"core_livingorganism"},
                locktype="transactionid",
            )
            return exact_wait(cursor, waiter.pid())

    def _assert_exact_organism_wait(self, observation, blocker_pid):
        self.assertEqual(observation["waiting"]["locktype"], "transactionid")
        self.assertIn(int(blocker_pid), observation["holders"])
        self.assertEqual(observation["tuple_relation"], "core_livingorganism")

    def _assert_ok(self, worker):
        result = worker.result(timeout=30)
        self.assertEqual(result, "ok")
        self.assertNotIn("deadlock detected", str(result).lower())

    def test_loi_x2_three_sessions_reentry_completes(self):
        """On the pre-0032 implementation, T2 ended in deadlock detected."""
        organisms = self._organism_pair(f"x2-{fresh()}")
        self._clear_stale_rows()  # TEST SETUP: start the three-session interleaving without stale rows.
        workers = []
        releases = [Event(), Event(), Event()]
        self.addCleanup(cleanup_workers, workers)
        self.addCleanup(_release_events, releases)
        t3_registered = Event()
        t1_registered = Event()
        t2_registered = Event()
        t3 = start_worker(
            lambda: _register_and_hold(organisms["second"]["uuid"], t3_registered, releases[0])
        )
        workers.append(t3)
        self.assertTrue(t3_registered.wait(timeout=10))
        t1 = start_worker(
            lambda: _register_and_hold(
                organisms["first"]["uuid"], t1_registered, releases[1], reenter=True,
            )
        )
        workers.append(t1)
        self.assertTrue(t1_registered.wait(timeout=10))

        releases[0].set()
        self._assert_ok(t3)
        t2 = start_worker(lambda: self._register(organisms["first"]["uuid"]))
        workers.append(t2)
        observation = self._exact_organism_wait(t2, t1)
        self._assert_exact_organism_wait(observation, t1.pid())
        releases[1].set()
        self._assert_ok(t1)
        self._assert_ok(t2)
        self.assertNotIn("deadlock detected", repr(observation).lower())

    def test_loi_x3_cross_lo_cleanup_does_not_wait_on_another_cleanups_row(self):
        organisms = self._organism_pair(f"x3-{fresh()}")
        self._seed_stale_row(organisms["first"]["uuid"])
        cleanup_module = importlib.import_module("core.migrations.0032_s015_register_cleanup_nonblocking")
        workers = []
        releases = [Event(), Event()]
        self.addCleanup(cleanup_workers, workers)
        self.addCleanup(_release_events, releases)
        cleanup_complete = Event()
        a = start_worker(
            lambda: _cleanup_and_hold(
                cleanup_module.CLEANUP_NONBLOCKING, cleanup_complete, releases[0],
            )
        )
        workers.append(a)
        self.assertTrue(cleanup_complete.wait(timeout=10))
        b_registered = Event()
        b = start_worker(
            lambda: _register_and_hold(
                organisms["second"]["uuid"], b_registered, releases[1],
            )
        )
        workers.append(b)
        self.assertTrue(b_registered.wait(timeout=10))
        with connection.cursor() as cursor:
            observation = lock_snapshot(cursor, b.pid())
        self.assertNotIn(a.pid(), observation["blockers"])
        self.assertFalse(releases[0].is_set())
        releases[1].set()
        self._assert_ok(b)
        self.assertTrue(a.thread.is_alive())
        releases[0].set()
        self._assert_ok(a)

    def test_live_registration_is_never_cleaned(self):
        """The eligibility rule protects a VISIBLE register row whose transaction is still in progress; once that transaction ends, a later
        cleanup removes it. (Another transaction's uncommitted register row is invisible to every other session, so it cannot be tested
        from outside; this test makes the in-progress case visible by test setup.)"""
        organisms = self._organism_pair(f"live-{fresh()}")
        self._clear_stale_rows()
        workers = []
        releases = [Event()]
        self.addCleanup(cleanup_workers, workers)
        self.addCleanup(_release_events, releases)
        held = Event()
        xids = []

        def hold():
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])", [organisms["first"]["uuid"]])
                cursor.execute("SELECT pg_current_xact_id()::text")
                xids.append(cursor.fetchone()[0])
                held.set()
                if not releases[0].wait(timeout=30):
                    raise TimeoutError("registration hold was not released")

        holder = start_worker(hold)
        workers.append(holder)
        self.assertTrue(held.wait(timeout=10))
        marker = uuid4()
        with connection.cursor() as cursor:  # TEST SETUP: a committed, visible register row carrying the LIVE holder's xid
            cursor.execute(
                "INSERT INTO public.s015_transaction_register (xid, kind, organism_uuid, ordinal) VALUES (%s::xid8, 'ORGANISM_LOCK', %s, 1)",
                [xids[0], marker],
            )
        other = start_worker(lambda: self._register(organisms["second"]["uuid"]))
        workers.append(other)
        self._assert_ok(other)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.s015_transaction_register WHERE organism_uuid = %s", [marker])
            self.assertEqual(cursor.fetchone()[0], 1, "a cleanup removed a row whose transaction is still in progress")
        releases[0].set()
        self._assert_ok(holder)
        later = start_worker(lambda: self._register(organisms["second"]["uuid"]))
        workers.append(later)
        self._assert_ok(later)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.s015_transaction_register WHERE organism_uuid = %s", [marker])
            self.assertEqual(cursor.fetchone()[0], 0, "a later cleanup did not remove the row once its transaction ended")

    def test_skipped_stale_row_is_cleaned_later(self):
        organisms = self._organism_pair(f"later-{fresh()}")
        self._seed_stale_row(organisms["first"]["uuid"])
        cleanup_module = importlib.import_module("core.migrations.0032_s015_register_cleanup_nonblocking")
        workers = []
        releases = [Event()]
        self.addCleanup(cleanup_workers, workers)
        self.addCleanup(_release_events, releases)
        cleanup_complete = Event()
        session_a = start_worker(
            lambda: _cleanup_and_hold(
                cleanup_module.CLEANUP_NONBLOCKING, cleanup_complete, releases[0], rollback=True,
            )
        )
        workers.append(session_a)
        self.assertTrue(cleanup_complete.wait(timeout=10))
        session_b = start_worker(lambda: self._register(organisms["second"]["uuid"]))
        workers.append(session_b)
        self._assert_ok(session_b)
        releases[0].set()
        self._assert_ok(session_a)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.s015_transaction_register "
                "WHERE kind = 'ORGANISM_LOCK' AND organism_uuid = %s::uuid",
                [organisms["first"]["uuid"]],
            )
            self.assertEqual(cursor.fetchone()[0], 1)
        session_c = start_worker(lambda: self._register(organisms["second"]["uuid"]))
        workers.append(session_c)
        self._assert_ok(session_c)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.s015_transaction_register "
                "WHERE kind = 'ORGANISM_LOCK' AND organism_uuid = %s::uuid",
                [organisms["first"]["uuid"]],
            )
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_stale_rows_do_not_change_required_locking(self):
        organisms = self._organism_pair(f"required-{fresh()}")
        self._seed_stale_row(organisms["first"]["uuid"])
        workers = []
        releases = [Event()]
        self.addCleanup(cleanup_workers, workers)
        self.addCleanup(_release_events, releases)
        holder_registered = Event()
        holder = start_worker(
            lambda: _register_and_hold(
                organisms["first"]["uuid"], holder_registered, releases[0],
            )
        )
        workers.append(holder)
        self.assertTrue(holder_registered.wait(timeout=10))
        waiter = start_worker(lambda: self._register(organisms["first"]["uuid"]))
        workers.append(waiter)
        observation = self._exact_organism_wait(waiter, holder)
        self._assert_exact_organism_wait(observation, holder.pid())
        releases[0].set()
        self._assert_ok(holder)
        self._assert_ok(waiter)

        organism_uuids = sorted((organisms["first"]["uuid"], organisms["second"]["uuid"]))
        lower_uuid, greater_uuid = organism_uuids
        self._seed_stale_row(lower_uuid)
        with self.assertRaises(IntegrityError) as refused:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                    [greater_uuid],
                )
                cursor.execute(
                    "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                    [lower_uuid],
                )
        expected = (
            f"S015 lock order: organism {lower_uuid} requested after greater organism {greater_uuid} in this transaction"
        )
        self.assertEqual(str(refused.exception).splitlines()[0], expected)

        self._seed_stale_row(organisms["first"]["uuid"])
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [organisms["first"]["uuid"]],
            )
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [organisms["first"]["uuid"]],
            )
            cursor.execute(
                "SELECT count(*) FROM public.s015_transaction_register "
                "WHERE kind = 'ORGANISM_LOCK' AND organism_uuid = %s::uuid "
                "AND xid = pg_current_xact_id()",
                [organisms["first"]["uuid"]],
            )
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_skip_locked_is_confined_to_the_cleanup(self):
        cleanup_module = importlib.import_module("core.migrations.0032_s015_register_cleanup_nonblocking")
        original_sql = cleanup_module.register_sql(False)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_get_functiondef('public.s015_register_organism_lock(uuid)'::regprocedure)"
            )
            installed = cursor.fetchone()[0]
            cursor.execute(
                "SELECT md5(pg_get_functiondef('public.s015_register_organism_lock(uuid)'::regprocedure))"
            )
            installed_md5 = cursor.fetchone()[0]
            upper = installed.upper()
            self.assertEqual(upper.count("SKIP LOCKED"), 1)
            self.assertNotIn("NOWAIT", upper)
            cleanup_start = upper.index("DELETE FROM PUBLIC.S015_TRANSACTION_REGISTER")
            cleanup_end = upper.index(";", cleanup_start)
            skip_position = upper.index("SKIP LOCKED")
            self.assertLess(cleanup_start, skip_position)
            self.assertLess(skip_position, cleanup_end)
            organism_lock_statement = (
                "SELECT id INTO v_found FROM public.core_livingorganism "
                "WHERE organism_id = p_organism_id FOR UPDATE;"
            )
            self.assertEqual(original_sql.count(organism_lock_statement), 1)
            self.assertEqual(installed.count(organism_lock_statement), 1)
            self.assertEqual(
                hashlib.md5(installed.encode("utf-8")).hexdigest(),
                _source_function_md5(cursor, cleanup_module.register_sql(True)),
            )
            self.assertEqual(installed_md5, hashlib.md5(installed.encode("utf-8")).hexdigest())

    def test_0032_apply_reverse_reapply(self):
        """The reverse restores the previous implementation including its known defect, and restores no operational clearance."""
        cleanup_module = importlib.import_module("core.migrations.0032_s015_register_cleanup_nonblocking")
        head = __import__("s015_0021_support").current_core_leaf(connection)
        executor = MigrationExecutor(connection)
        triggers_at_head = trigger_names()
        try:
            executor.migrate([("core", "0032_s015_register_cleanup_nonblocking")])
            triggers_at_0032 = trigger_names()
            MigrationExecutor(connection).migrate([("core", "0031_s1fu_admission_actor_requirement")])
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT md5(pg_get_functiondef('public.s015_register_organism_lock(uuid)'::regprocedure))"
                )
                original_md5 = cursor.fetchone()[0]
                self.assertEqual(
                    original_md5,
                    _source_function_md5(cursor, cleanup_module.register_sql(False)),
                )
            # Brittle-test repair (27 Oct 2026): 0032 replaces a function only, so its reverse changes no trigger;
            # asserted as an empty delta, not as a frozen schema-wide total at 0031.
            assert_trigger_delta(self, triggers_at_0032, trigger_names())

            MigrationExecutor(connection).migrate(head)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT md5(pg_get_functiondef('public.s015_register_organism_lock(uuid)'::regprocedure))"
                )
                self.assertEqual(
                    cursor.fetchone()[0],
                    _source_function_md5(cursor, cleanup_module.register_sql(True)),
                )
            self.assertEqual(trigger_names(), triggers_at_head)
        finally:
            MigrationExecutor(connection).migrate(head)

    @staticmethod
    def _register(organism_uuid):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_lock_organisms(ARRAY[%s]::uuid[])",
                [organism_uuid],
            )
