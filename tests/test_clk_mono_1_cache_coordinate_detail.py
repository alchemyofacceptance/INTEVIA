"""CLK-MONO-1 diagnostic (migration 0050) - the focused check (PKT-CLK-3 CLK3-3, LD return attempt 83 v1.1, module v1.2).

Proves, on the database the run uses: (1) forward application installed 0050's two function bodies with their
security attributes and the five cache-compute trigger bindings in place; (2) a normal guarded refresh still passes
both checks and returns a current-form (s015f4) fingerprint; (3) a deliberately exercised refusal on each path - the
refresh's and the trigger's - keeps the unchanged primary message and SQLSTATE and carries a DETAIL whose five
timestamp values parse, name the supplied coordinates exactly and show known_at ahead of the reading, and the failed
write is rolled back; (4) reverse restores 0021's historical bodies and forward reinstalls 0050's, with the trigger
bindings, the functions' identities (OIDs) and their security attributes unchanged throughout.
Node identity: tests/test_clk_mono_1_cache_coordinate_detail.py::<Class>::<test>.

Owner powers used (practice item 121), three:
(1) RefusalPaths.test_..._4 inserts a REFRESH_TICKET directly into public.s015_transaction_register. The refresh
    function checks the coordinates before it writes a ticket, so the trigger's own check cannot be reached through
    the refresh; a directly written ticket is the only way to it. 0021 revokes the register from PUBLIC, so the test
    connection's role must be authorised to insert there - the role that ran the migrations is, as in the approved
    runner. A permission failure here is to be reported, never worked around with a grant.
(2) ReverseThenForward runs 0050's backwards and forwards through MigrationExecutor: CREATE OR REPLACE FUNCTION
    by the function-owning role, the power every migration-reversal test already uses.
(3) RefusalPaths.setUp/tearDown drop and re-add the platform-root constraint s015_platform_root_unset_ck on
    core_livingorganism (ALTER TABLE), the _S015NegativeBase scaffold verbatim; the re-add is NOT VALID, as there.

What is rolled back and what is not: the deliberately failing ticket-and-coordinate write (test 4), the refused
refresh (test 3) and the migration reversal and reapplication (test 5) each run inside a transaction that fails or is
deliberately rolled back, so none of them leaves a row or a catalog change. The founded root fixture (tests 2-4), the
successful refresh (test 2) and the constraint drop and re-add (setUp/tearDown) are committed writes: they rely on the
test lifecycle (TransactionTestCase flush, tearDown) and on the isolation runner's per-test comparison and disposable
database for cleanup. This module is therefore for the approved runner's clone-mode database, not for a shared or
template database. No superuser; no grant, ownership or role change.
"""
import importlib
import re
from datetime import datetime
from unittest import skipUnless

from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

from s015_0021_support import T0, current_applied_core_head, drop_unset_check, first_line, found_root

import verification.isolation.state as isolation_state

POSTGRESQL_ONLY = skipUnless(connection.vendor == "postgresql", "0050 replaces PostgreSQL functions")
MIGRATION = ("core", "0050_clk_mono_1_cache_coordinate_detail")
PREVIOUS = ("core", "0049_h100_command_intake")
LABEL = "S015 cache coordinate diagnostic: anchor="
TRIGGER_MESSAGE = "S015 cache coordinates must satisfy state_at <= known_at <= now"
REFRESH_MESSAGE = "S015 refresh: coordinates must satisfy state_at <= known_at <= now"
COMPUTE, REFRESH = "s015_compute_governed_cache", "s015_refresh_governed_cache"
SEARCH_PATH = "search_path=pg_catalog, public, pg_temp"
FINGERPRINT_FORM = r"^s015f4:[0-9a-f]{64}$"  # the current canonical form: migration 0023 moved s015f3 to s015f4
TIMESTAMP_FIELDS = ("state_at", "known_at", "now", "statement_start", "xact_start")
DETAIL_FORM = re.compile(
    r"^S015 cache coordinate diagnostic: anchor=(?P<anchor>\S+) id=(?P<id>\d+) state_at=(?P<state_at>.+?) "
    r"known_at=(?P<known_at>.+?) now=(?P<now>.+?) statement_start=(?P<statement_start>.+?) xact_start=(?P<xact_start>.+)$"
)
COORDINATES_SQL = (
    "SELECT state, current_state_effective_at, state_known_at, state_source_event_set_fingerprint "
    "FROM public.core_livingorganism WHERE id = %s"
)


def _module():
    return importlib.import_module("core.migrations." + MIGRATION[1])


def _body(create_function_sql):
    """The dollar-quoted body as pg_proc.prosrc stores it (the test_se_entitlement pattern)."""
    return create_function_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]


def _installed_bodies():
    """prosrc by name; exactly one function of each name in public (the identity is checked by OID in _security_attributes)."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT p.proname, p.prosrc FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = ANY(%s)",
            [[COMPUTE, REFRESH]],
        )
        rows = cur.fetchall()
    if len(rows) != 2:
        raise AssertionError("expected exactly one %s and one %s in public, found %r" % (COMPUTE, REFRESH, rows))
    return dict(rows)


def _security_attributes():
    with connection.cursor() as cur:
        cur.execute(
            "SELECT p.proname, p.oid, p.prosecdef, p.proconfig, pg_catalog.pg_get_userbyid(p.proowner), p.proacl::text, "
            "pg_catalog.pg_get_function_identity_arguments(p.oid), p.prorettype::regtype::text, l.lanname "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
            "JOIN pg_catalog.pg_language l ON l.oid = p.prolang "
            "WHERE n.nspname = 'public' AND p.proname = ANY(%s) ORDER BY p.proname",
            [[COMPUTE, REFRESH]],
        )
        return [tuple(r) for r in cur.fetchall()]


def _cache_compute_bindings():
    with connection.cursor() as cur:
        cur.execute(
            "SELECT t.tgname, t.tgrelid::regclass::text, t.tgfoid, p.proname, t.tgenabled, t.tgtype "
            "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid "
            "WHERE NOT t.tgisinternal AND p.proname = %s ORDER BY t.tgname",
            [COMPUTE],
        )
        return [tuple(r) for r in cur.fetchall()]


def _trigger_count():
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND NOT t.tgisinternal"
        )
        return cur.fetchone()[0]


def _expected_cache_compute_bindings():
    migration_0021 = importlib.import_module("core.migrations.0021_s015_recorded_chain_and_bitemporal_fold")
    return sorted(anchor[0] for anchor in migration_0021.CACHE_ANCHORS)


def _assert_0050_installed(test_case, bindings_before, security_before):
    bodies = _installed_bodies()
    module = _module()
    test_case.assertEqual(bodies[COMPUTE], _body(module._COMPUTE))
    test_case.assertEqual(bodies[REFRESH], _body(module._REFRESH))
    test_case.assertEqual(_cache_compute_bindings(), bindings_before)
    test_case.assertEqual(_security_attributes(), security_before)


@POSTGRESQL_ONLY
class ForwardInstallation(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def test_w_clk3_0050_1_forward_installed_bodies_bindings_and_security(self):
        module = _module()
        bodies = _installed_bodies()
        self.assertEqual(sorted(bodies), [COMPUTE, REFRESH])
        self.assertEqual(bodies[COMPUTE], _body(module._COMPUTE))
        self.assertEqual(bodies[REFRESH], _body(module._REFRESH))
        self.assertIn(LABEL, bodies[COMPUTE])
        self.assertIn(LABEL, bodies[REFRESH])
        bindings = _cache_compute_bindings()
        self.assertEqual(len(bindings), 5)
        self.assertEqual(sorted(b[1].replace("public.", "") for b in bindings), _expected_cache_compute_bindings())
        self.assertEqual(len({b[2] for b in bindings}), 1)  # the five triggers name one function OID
        for tgname, _table, _tgfoid, proname, tgenabled, _tgtype in bindings:
            self.assertTrue(tgname.startswith("s015_0021_") and tgname.endswith("_cache_compute"), tgname)
            self.assertEqual(proname, COMPUTE)
            self.assertEqual(tgenabled, "O")
        attributes = _security_attributes()
        self.assertEqual([row[0] for row in attributes], [COMPUTE, REFRESH])
        for proname, _oid, prosecdef, proconfig, _owner, _acl, _args, _rettype, lanname in attributes:
            self.assertTrue(prosecdef, proname)
            self.assertEqual(proconfig, [SEARCH_PATH], proname)
            self.assertEqual(lanname, "plpgsql", proname)
        self.assertEqual(current_applied_core_head(connection), [MIGRATION])
        self.assertEqual(_trigger_count(), isolation_state.REQUIRED_TRIGGER_COUNT[MIGRATION[1]])


@POSTGRESQL_ONLY
class RefusalPaths(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def setUp(self):
        super().setUp()
        with connection.cursor() as cur:
            drop_unset_check(cur)

    def tearDown(self):
        with connection.cursor() as cur:
            cur.execute(
                "ALTER TABLE public.core_livingorganism ADD CONSTRAINT "
                "s015_platform_root_unset_ck CHECK (NOT platform_root) NOT VALID"
            )
        super().tearDown()

    def _root(self):
        with transaction.atomic():
            with connection.cursor() as cur:
                return found_root(cur)

    def _coordinates(self, lo_id):
        with connection.cursor() as cur:
            cur.execute(COORDINATES_SQL, [lo_id])
            return cur.fetchone()

    def _tickets(self, lo_id):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM public.s015_transaction_register "
                "WHERE kind = 'REFRESH_TICKET' AND anchor_token = 'core_livingorganism' AND anchor_id = %s",
                [lo_id],
            )
            return cur.fetchone()[0]

    def _future_known_at(self):
        """One future coordinate from PostgreSQL, retained and passed in exactly, so DETAIL's known_at can be compared."""
        with connection.cursor() as cur:
            cur.execute("SELECT clock_timestamp() + interval '1 day'")
            return cur.fetchone()[0]

    def _refused(self, fn):
        """Run fn(cur) in an outermost atomic block; return the psycopg cause of the refusal (block rolled back)."""
        with self.assertRaises(DatabaseError) as refused:
            with transaction.atomic():
                with connection.cursor() as cur:
                    fn(cur)
        cause = refused.exception.__cause__
        self.assertIsNotNone(cause)
        return refused.exception, cause

    def _parse_detail(self, detail, lo_id):
        """The DETAIL's seven fields; the five timestamps parsed by PostgreSQL itself into timezone-aware datetimes."""
        self.assertIsNotNone(detail)
        match = DETAIL_FORM.match(detail)
        self.assertIsNotNone(match, detail)
        self.assertEqual((match.group("anchor"), int(match.group("id"))), ("core_livingorganism", lo_id))
        texts = [match.group(name) for name in TIMESTAMP_FIELDS]
        for name, text in zip(TIMESTAMP_FIELDS, texts):
            self.assertTrue(text.strip(), "%s is empty in %r" % (name, detail))
            self.assertNotIn("%", text)
        with connection.cursor() as cur:
            cur.execute("SELECT " + ", ".join(["%s::timestamptz"] * len(texts)), texts)
            parsed = cur.fetchone()
        values = dict(zip(TIMESTAMP_FIELDS, parsed))
        for name, value in values.items():
            self.assertIsInstance(value, datetime, name)
            self.assertIsNotNone(value.tzinfo, name)
        return values

    def _assert_detail(self, detail, lo_id, known_at):
        values = self._parse_detail(detail, lo_id)
        self.assertEqual(values["state_at"], T0)
        self.assertEqual(values["known_at"], known_at)
        self.assertGreater(values["known_at"], values["now"])

    def test_w_clk3_0050_2_normal_guarded_refresh_passes_both_checks(self):
        ids = self._root()
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT public.s015_refresh_governed_cache('core_livingorganism', %s, %s, clock_timestamp())",
                    (ids["lo"], T0),
                )
                fp = cur.fetchone()[0]
        self.assertRegex(fp, FINGERPRINT_FORM)
        state, state_at, known_at, stored_fp = self._coordinates(ids["lo"])
        self.assertEqual((state_at, stored_fp), (T0, fp))
        with connection.cursor() as cur:
            cur.execute("SELECT %s <= clock_timestamp()", [known_at])
            self.assertTrue(cur.fetchone()[0])
        self.assertEqual(self._tickets(ids["lo"]), 0)

    def test_w_clk3_0050_3_refresh_refusal_keeps_message_and_sqlstate_and_reports_detail(self):
        ids = self._root()
        before = self._coordinates(ids["lo"])
        future_known_at = self._future_known_at()
        exc, cause = self._refused(lambda cur: cur.execute(
            "SELECT public.s015_refresh_governed_cache('core_livingorganism', %s, %s, %s)",
            (ids["lo"], T0, future_known_at),
        ))
        self.assertEqual(cause.diag.message_primary, REFRESH_MESSAGE)
        self.assertEqual(cause.diag.sqlstate, "P0001")
        self._assert_detail(cause.diag.message_detail, ids["lo"], future_known_at)
        self.assertIn(REFRESH_MESSAGE, first_line(exc))
        self.assertIn("DETAIL:", str(exc))
        self.assertEqual(self._coordinates(ids["lo"]), before)
        self.assertEqual(self._tickets(ids["lo"]), 0)

    def test_w_clk3_0050_4_trigger_refusal_keeps_message_and_sqlstate_and_reports_detail(self):
        ids = self._root()
        before = self._coordinates(ids["lo"])
        future_known_at = self._future_known_at()

        def ticket_then_future_known_at(cur):
            # owner power (1): a REFRESH_TICKET written directly, so that the trigger's own check is the one that refuses
            cur.execute(
                "INSERT INTO public.s015_transaction_register (xid, kind, anchor_token, anchor_id) "
                "VALUES (pg_current_xact_id(), 'REFRESH_TICKET', 'core_livingorganism', %s)",
                [ids["lo"]],
            )
            cur.execute(
                "UPDATE public.core_livingorganism SET current_state_effective_at = %s, state_known_at = %s WHERE id = %s",
                (T0, future_known_at, ids["lo"]),
            )

        exc, cause = self._refused(ticket_then_future_known_at)
        self.assertEqual(cause.diag.message_primary, TRIGGER_MESSAGE)
        self.assertEqual(cause.diag.sqlstate, "23000")
        self._assert_detail(cause.diag.message_detail, ids["lo"], future_known_at)
        self.assertIn(TRIGGER_MESSAGE, first_line(exc))
        self.assertIn("DETAIL:", str(exc))
        self.assertEqual(self._coordinates(ids["lo"]), before)
        self.assertEqual(self._tickets(ids["lo"]), 0)


@POSTGRESQL_ONLY
class ReverseThenForward(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def test_w_clk3_0050_5_reverse_restores_0021_bodies_forward_reinstalls_0050(self):
        class _Rollback(Exception):
            pass

        module = _module()
        bindings_before = _cache_compute_bindings()
        security_before = _security_attributes()
        self.assertEqual(len(bindings_before), 5)
        _assert_0050_installed(self, bindings_before, security_before)
        with self.assertRaises(_Rollback):
            with transaction.atomic():
                MigrationExecutor(connection).migrate([PREVIOUS])
                self.assertNotIn(MIGRATION, MigrationRecorder(connection).applied_migrations())
                self.assertEqual(current_applied_core_head(connection), [PREVIOUS])
                bodies = _installed_bodies()
                self.assertEqual(bodies[COMPUTE], _body(module._PREVIOUS_COMPUTE))
                self.assertEqual(bodies[REFRESH], _body(module._PREVIOUS_REFRESH))
                self.assertNotIn(LABEL, bodies[COMPUTE])
                self.assertNotIn(LABEL, bodies[REFRESH])
                self.assertEqual(_cache_compute_bindings(), bindings_before)
                self.assertEqual(_security_attributes(), security_before)
                self.assertEqual(_trigger_count(), isolation_state.REQUIRED_TRIGGER_COUNT[PREVIOUS[1]])
                MigrationExecutor(connection).migrate([MIGRATION])
                self.assertIn(MIGRATION, MigrationRecorder(connection).applied_migrations())
                self.assertEqual(current_applied_core_head(connection), [MIGRATION])
                _assert_0050_installed(self, bindings_before, security_before)
                self.assertEqual(_trigger_count(), isolation_state.REQUIRED_TRIGGER_COUNT[MIGRATION[1]])
                raise _Rollback()
        _assert_0050_installed(self, bindings_before, security_before)
        self.assertEqual(current_applied_core_head(connection), [MIGRATION])
