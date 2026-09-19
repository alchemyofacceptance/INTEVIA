"""V5 discriminating cases, V3 deliberately failed resets and the seed-restoration check (candidate v0.4).
v0.4 (UFUND-2): N1-N4 prove checkpoint applicability is taken from effective database permissions.
core_role.created_at is NOT NULL with no database default (Django auto_now_add), so raw inserts supply it;
v0.2 omitted it, and the ISO_20260916T144341Z trial errored on it in V3b, V3b-negative and V5a.
Run under IsolationRunner on its own qualified disposable database. Each discriminating check must FAIL at
its intended point against a deliberately wrong state; mutations are made inside a transaction and rolled
back unless stated."""
import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from . import state
from .runner import CURRENT

GUARDED_TABLE, GUARDIAN = "core_livingorganism", "s015_livingorganism_truncate_immutable"
PROBE_TABLE = "ufund1_isolation_probe"
SENTINEL = "UFUND1_SENTINEL"
V3B_INJECTED = "UFUND1_V3B_INJECTED_AFTER_TRUNCATE"
V3B_NOT_AFTER_TRUNCATE = "UFUND1_V3B_NOT_AFTER_TRUNCATE"
V3B_GUARDIAN_NOT_SUSPENDED = "UFUND1_V3B_GUARDIAN_NOT_SUSPENDED"
DB_PATTERN = re.compile(r"test_intevia_living_organism_[a-z0-9_]+\Z")


class _Rollback(Exception):
    pass


def v3b_injection_block():
    """Raises the intended error only if it executes after TRUNCATE (sentinel gone) and while the
    guardian is suspended; otherwise raises a different, identifiable error."""
    return (
        "DO $u$ BEGIN "
        f"IF (SELECT count(*) FROM core_role WHERE name = '{SENTINEL}') <> 0 THEN "
        f"RAISE EXCEPTION '{V3B_NOT_AFTER_TRUNCATE}'; END IF; "
        "IF EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        f"WHERE c.relname = '{GUARDED_TABLE}' AND t.tgname = '{GUARDIAN}' AND t.tgenabled <> 'D') THEN "
        f"RAISE EXCEPTION '{V3B_GUARDIAN_NOT_SUSPENDED}'; END IF; "
        f"RAISE EXCEPTION '{V3B_INJECTED}'; "
        "END $u$;"
    )


def classify_v3b(raised):
    """(passed, reason) for the exception a V3b attempt raised, or None if nothing was raised."""
    if raised is None:
        return False, "no error raised"
    if not isinstance(raised, DatabaseError):
        return False, "not a database error: %s" % type(raised).__name__
    message = str(raised)
    for marker in (V3B_NOT_AFTER_TRUNCATE, V3B_GUARDIAN_NOT_SUSPENDED):
        if marker in message:
            return False, "injection reached at the wrong point: %s" % marker
    if V3B_INJECTED not in message:
        return False, "intended injected error not raised: %s" % message.splitlines()[0][:160]
    return True, "intended injected error raised after truncation with the guardian suspended"


class IsolationSelfChecks(TransactionTestCase):
    def reference(self):
        runner = CURRENT["runner"]
        self.assertIsNotNone(runner, "must run under verification.isolation.runner.IsolationRunner")
        self.assertIsNotNone(runner.reference, "CP-0 requirements not met; self-checks cannot run")
        return runner.reference

    def detect_inside_rollback(self, mutate, expect_data=False, expect_protection=False):
        ref = self.reference()
        seen = {}
        try:
            with transaction.atomic():
                with connection.cursor() as cur:
                    mutate(cur)
                seen["data"], seen["prot"] = state.compare(ref, state.capture())
                raise _Rollback()
        except _Rollback:
            pass
        if expect_data:
            self.assertTrue(seen["data"], "V5: data check did not detect the deliberate row")
        if expect_protection:
            self.assertTrue(seen["prot"], "V5: protection check did not detect the deliberate change")
        self.assertEqual(state.compare(ref, state.capture()), ([], []), "rollback did not restore the reference state")

    # ---------------------------------------------------------------- V5 discriminating cases
    def test_v5a_data_check_detects_retained_row(self):
        self.detect_inside_rollback(
            lambda cur: cur.execute("INSERT INTO core_role (name, description, created_at) VALUES ('UFUND1_PROBE', 'probe', now())"),
            expect_data=True)

    def test_v5b_protection_check_detects_disabled_guardian(self):
        self.detect_inside_rollback(
            lambda cur: cur.execute('ALTER TABLE "%s" DISABLE TRIGGER "%s"' % (GUARDED_TABLE, GUARDIAN)),
            expect_protection=True)

    def test_v5c_protection_check_detects_unvalidated_constraint(self):
        def mutate(cur):
            cur.execute("ALTER TABLE public.core_livingorganism DROP CONSTRAINT s015_platform_root_unset_ck")
            cur.execute("ALTER TABLE public.core_livingorganism ADD CONSTRAINT s015_platform_root_unset_ck CHECK (NOT platform_root) NOT VALID")
        self.detect_inside_rollback(mutate, expect_protection=True)

    def test_v5d_protection_check_detects_replaced_function_body(self):
        self.detect_inside_rollback(
            lambda cur: cur.execute(
                "CREATE OR REPLACE FUNCTION public.s015_refuse_row_mutation() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN RETURN NULL; END; $$"),
            expect_protection=True)

    def test_v5e_protection_check_detects_changed_grant(self):
        self.detect_inside_rollback(
            lambda cur: cur.execute("GRANT INSERT ON public.s015_transaction_register TO PUBLIC"),
            expect_protection=True)

    def test_v5f_protection_check_detects_missing_constraint(self):
        self.detect_inside_rollback(
            lambda cur: cur.execute("ALTER TABLE public.core_livingorganism DROP CONSTRAINT s015_platform_root_unset_ck"),
            expect_protection=True)

    # ---------------------------------------------------------------- seed restoration on the qualified connection
    def test_seed_restored_by_reset_on_qualified_connection(self):
        self.reference()
        with connection.cursor() as cur:
            cur.execute("SELECT current_database()")
            name = cur.fetchone()[0]
        self.assertEqual(name, settings.DATABASES["default"]["NAME"])
        self.assertIsNotNone(DB_PATTERN.fullmatch(name), "not a qualified disposable database: %s" % name)
        with connection.cursor() as cur:
            cur.execute("DELETE FROM core_role")
        call_command("flush", verbosity=0, interactive=False)
        self.assertEqual(state.capture()["core_role"], state.expected_seed_rows(),
                         "reset did not restore 0003's seed rows")

    # ---------------------------------------------------------------- V3 deliberately failed resets
    def _guardian_rows(self):
        return [r for r in state.capture()["triggers"] if (r[3] & 32)]

    def _flush_sql(self):
        tables = connection.introspection.django_table_names(only_existing=True, include_views=False)
        return connection.ops.sql_flush(no_style(), tables)

    def test_v3a_undeclared_table_refuses_reset_and_leaves_protections_unchanged(self):
        ref = self.reference()
        runner = CURRENT["runner"]
        with connection.cursor() as cur:
            cur.execute("CREATE TABLE public.%s (id integer)" % PROBE_TABLE)
        runner.ignore_tables = (PROBE_TABLE,)
        try:
            before = state.capture()
            with self.assertRaisesMessage(ImproperlyConfigured, "S015 reset refuses undeclared unmodelled tables"):
                call_command("flush", verbosity=0, interactive=False, inhibit_post_migrate=True)
            after = state.capture()
            self.assertEqual(before["triggers"], after["triggers"], "V3a: triggers changed across the refused reset")
            self.assertEqual(before["constraints"], after["constraints"], "V3a: constraints changed across the refused reset")
        finally:
            with connection.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS public.%s" % PROBE_TABLE)
            runner.ignore_tables = ()
        self.assertEqual(state.compare(ref, state.capture()), ([], []), "V3a: required starting state not re-established")

    def _attempt(self, build):
        """Insert the sentinel, run a deliberately broken reset, and return (raised, guardians_before, guardians_after, sentinel_after)."""
        with connection.cursor() as cur:
            cur.execute("INSERT INTO core_role (name, description, created_at) VALUES (%s, 'sentinel', now())", [SENTINEL])
        sql = self._flush_sql()
        truncate_at = next(i for i, s in enumerate(sql) if s.startswith("TRUNCATE"))
        self.assertLess(max(i for i, s in enumerate(sql) if "DISABLE TRIGGER" in s), truncate_at)
        broken = build(sql, truncate_at)
        before = self._guardian_rows()
        raised = None
        try:
            connection.ops.execute_sql_flush(broken)
        except Exception as exc:  # classified below; nothing is accepted here
            raised = exc
        after = self._guardian_rows()
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM core_role WHERE name = %s", [SENTINEL])
            sentinel = cur.fetchone()[0]
        return raised, before, after, sentinel

    def test_v3b_failure_after_truncate_rolls_back_disabled_guardians(self):
        ref = self.reference()
        raised, before, after, sentinel = self._attempt(
            lambda sql, t: sql[: t + 1] + [v3b_injection_block()] + sql[t + 1:])
        passed, reason = classify_v3b(raised)
        self.assertTrue(passed, "V3b: " + reason)
        self.assertTrue(before and all(r[2] == "O" for r in before), "V3b: guardians not enabled before the attempt")
        self.assertEqual(before, after, "V3b: guardian state differs after the failed reset")
        self.assertEqual(sentinel, 1, "V3b: the failed reset was not rolled back as a whole")
        call_command("flush", verbosity=0, interactive=False)
        self.assertEqual(state.compare(ref, state.capture()), ([], []), "V3b: required starting state not re-established")

    def test_v3b_negative_earlier_unrelated_error_cannot_pass(self):
        ref = self.reference()
        raised, before, after, sentinel = self._attempt(
            lambda sql, t: ["SELECT 1/0;"] + sql[: t + 1] + [v3b_injection_block()] + sql[t + 1:])
        passed, reason = classify_v3b(raised)
        self.assertFalse(passed, "V3b classifier accepted an unrelated earlier error")
        self.assertIn("intended injected error not raised", reason)
        self.assertEqual(before, after)
        self.assertEqual(sentinel, 1)
        call_command("flush", verbosity=0, interactive=False)
        self.assertEqual(state.compare(ref, state.capture()), ([], []), "V3b negative: required starting state not re-established")

    def test_v3c_missing_restoration_is_detected_and_rolled_back(self):
        ref = self.reference()
        sql = [s for s in self._flush_sql() if "ENABLE TRIGGER" not in s]
        before = self._guardian_rows()
        with self.assertRaisesMessage(DatabaseError, "S015 truncate guardian restoration failed"):
            connection.ops.execute_sql_flush(sql)
        self.assertEqual(before, self._guardian_rows(), "V3c: guardians not restored after a failed restoration check")
        self.assertEqual(state.compare(ref, state.capture())[1], [], "V3c: protections differ from the reference")



# ---------------------------------------------------------------------------------------------------------------
# N1-N4 (v0.4): checkpoint applicability. These run in a SimpleTestCase, so their own CP-A is itself not applicable -
# a live instance of the classification they test.
# ---------------------------------------------------------------------------------------------------------------
from django.test import SimpleTestCase, TestCase

from .runner import database_access, isolation_verdict


def _probes():
    """Probe test-case classes, built inside a function so the test loader never collects or runs them."""
    class SimpleProbe(SimpleTestCase):
        def runTest(self):
            pass

    class SimpleProbeWithDefault(SimpleTestCase):
        databases = {"default"}

        def runTest(self):
            pass

    class TransactionProbe(TransactionTestCase):
        def runTest(self):
            pass

    class TestCaseAllProbe(TestCase):
        databases = "__all__"

        def runTest(self):
            pass

    return SimpleProbe, SimpleProbeWithDefault, TransactionProbe, TestCaseAllProbe


def _cp(test, established, applicable=True, access="permitted"):
    return {"checkpoint": "CP-A", "test": test, "established": established, "applicable": applicable, "database_access": access}


class CheckpointApplicabilitySelfChecks(SimpleTestCase):
    def test_n1_access_is_taken_from_effective_databases_not_class_name(self):
        simple, simple_with_default, transaction_probe, all_probe = _probes()
        self.assertEqual(database_access(simple())[0], "forbidden")
        self.assertEqual(database_access(simple_with_default())[0], "permitted")  # a SimpleTestCase granted the database
        self.assertEqual(database_access(transaction_probe())[0], "permitted")
        self.assertEqual(database_access(all_probe())[0], "permitted")
        self.assertEqual(database_access(object())[0], "unknown")

    def test_n2_database_enabled_tests_remain_checked(self):
        suite = ["a", "b"]
        verdict, reasons = isolation_verdict(True, suite, suite, suite, [_cp("a", True), _cp("b", False)], 0)
        self.assertEqual(verdict, "NOT ESTABLISHED")
        self.assertTrue(any("not established" in r for r in reasons))

    def test_n3_not_applicable_counts_only_where_access_is_forbidden(self):
        suite = ["a", "b"]
        lawful = [_cp("a", True), _cp("b", None, applicable=False, access="forbidden")]
        self.assertEqual(isolation_verdict(True, suite, suite, suite, lawful, 0)[0], "PASS")
        unlawful = [_cp("a", True), _cp("b", None, applicable=False, access="permitted")]
        self.assertEqual(isolation_verdict(True, suite, suite, suite, unlawful, 0)[0], "NOT ESTABLISHED")

    def test_n4_failed_applicable_checkpoint_prevents_pass_despite_not_applicable_ones(self):
        suite = ["a", "b", "c"]
        checkpoints = [_cp("a", None, applicable=False, access="forbidden"), _cp("b", None, applicable=False, access="forbidden"), _cp("c", False)]
        self.assertEqual(isolation_verdict(True, suite, suite, suite, checkpoints, 0)[0], "NOT ESTABLISHED")
