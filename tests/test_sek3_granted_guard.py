"""PKT-SE-K3 database guard and Annex P migration witnesses."""

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import (
    AuthorityDecision,
    CommandIntake,
    GovernedRule,
    LivingOrganismEvent,
    LOApplicationSettingsVersion,
    OrganismCommandReceipt,
    OrganismEntitlement,
    OrganismEntitlementEvent,
    OrganismEntitlementPolicyText,
)
from src.intevia.services.organism_entitlement_service import (
    OrganismEntitlementService,
    S015EntitlementTechnicalFailure,
    _is_entitlement_granted_guard_failure,
)
from src.intevia.services.organism_formation_service import (
    S015BirthingTechnicalFailure,
)
from tests.s015_0021_support import current_applied_core_head, database_now
from tests.sb2_route_support import prepare_route
from tests.se_route_support import (
    EVENT_LESS_ENTITLEMENT_DECLARATIONS,
    EventLessEntitlementRestorationError,
    declared_event_less_entitlement_rows,
    ensure_test_policy_text,
)
from verification.isolation import state as isolation_state
from tests.test_se_entitlement import (
    _birthing_command,
    _election_command,
    _seed_beta_entitlement,
    _table_state,
)


MIGRATION = ("core", "0048_se_entitlement_granted_event_guard")
PREVIOUS = ("core", "0047_sek1_platform_rule_statement_start")
TRIGGER_NAME = "s015_organismentitlement_granted_guard"
FUNCTION_NAME = "s015_guard_entitlement_granted"
T1 = (
    "Beta access could not be activated because the system could not confirm "
    "the required record of the grant. This attempt made no changes. This is a "
    "system recording fault, not a refusal of your organisation’s eligibility "
    "for beta access. The fault needs to be checked before you retry."
)
BIRTHING_FAILURE = (
    "BIRTHING could not be completed because of a technical problem; "
    "nothing was saved. Try again; if it persists, report it."
)
EVENT_STATE_MODELS = (
    OrganismEntitlementPolicyText,
    OrganismEntitlement,
    OrganismEntitlementEvent,
    LOApplicationSettingsVersion,
    LivingOrganismEvent,
    OrganismCommandReceipt,
    AuthorityDecision,
)


def _records_state():
    return tuple(_table_state(model) for model in EVENT_STATE_MODELS)


def _trigger_count():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal"
        )
        return cursor.fetchone()[0]


def _function_count():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = %s",
            [FUNCTION_NAME],
        )
        return cursor.fetchone()[0]


def _guard_catalogue():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.tgenabled, t.tgdeferrable, t.tginitdeferred, p.proname
              FROM pg_trigger t
              JOIN pg_class r ON r.oid = t.tgrelid
              JOIN pg_namespace n ON n.oid = r.relnamespace
              JOIN pg_proc p ON p.oid = t.tgfoid
             WHERE n.nspname = 'public'
               AND r.relname = 'core_organismentitlement'
               AND t.tgname = %s
            """,
            [TRIGGER_NAME],
        )
        return cursor.fetchall()


def _assert_guard_present(test_case):
    test_case.assertEqual(
        _guard_catalogue(),
        [("O", True, True, FUNCTION_NAME)],
    )


def _assert_guard_absent(test_case):
    test_case.assertEqual(_guard_catalogue(), [])
    test_case.assertEqual(_function_count(), 0)


def _migration_is_recorded():
    return MIGRATION in MigrationRecorder(connection).applied_migrations()


def _assert_applied_core_head(test_case, expected):
    test_case.assertEqual(current_applied_core_head(connection)[0][1], expected)


def _create_eventless_beta(route, *, limits=None, use_bulk_insert=False):
    policy_version, _ = ensure_test_policy_text()
    rule = GovernedRule.objects.get(code="BETA_COMPLIMENTARY_ACCESS")
    entitlement = OrganismEntitlement(
        living_organism=route.organism,
        source="COMPLIMENTARY",
        basis="BETA_OPT_IN",
        granting_party_id=None,
        policy_reference=f"policy-text:v{policy_version}",
        policy_version=policy_version,
        capabilities=rule.body["capabilities"],
        limits=rule.body["limits"] if limits is None else limits,
        effective_at=database_now(),
        effective_until=None,
        ending_condition_reference=(
            f"paid-availability-transition:{route.organism.organism_id}"
        ),
        tier_reference=None,
        pricing_basis_reference=None,
    )
    if use_bulk_insert:
        return OrganismEntitlement.objects.bulk_create([entitlement])[0]
    entitlement.save(force_insert=True)
    return entitlement


class _EventlessComposer(OrganismEntitlementService):
    def __init__(self, route):
        super().__init__(authority=route.birthing_service._authority, clock=database_now)
        self._fixture_route = route

    def _compose_beta_grant(self, **kwargs):
        return _create_eventless_beta(self._fixture_route)


@contextmanager
def _rollback_migration_work(test_case):
    class _Rollback(Exception):
        pass

    with test_case.assertRaises(_Rollback):
        with transaction.atomic():
            yield
            raise _Rollback()


class SEGrantedGuardMigrationTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True
    reset_sequences = True

    def test_w_se_k3_2_f_valid_rows_survive_forward_and_reverse_refuses_data(self):
        # H100 chunk 4 tests v1.4 (R-1): the entitlement fixtures need only an organism with an LO
        # event. A route with no gateway effect (FOUND and one phase advance, neither an admitted
        # entry) leaves no core_commandintake row, so the migration to 0047 passes 0049's reverse,
        # which refuses while intake rows exist (the table refuses DELETE and TRUNCATE).
        route = prepare_route(
            tag=f"k3-migration-valid-{uuid4().hex}",
            governance_enabled=None,
            include_settings=False,
            include_question=False,
            to_birthing_phase=False,
        )
        self.assertFalse(CommandIntake.objects.exists())
        before = _records_state()
        original_trigger_count = _trigger_count()
        observed = []

        def capture(execute, sql, params, many, context):
            normalized = " ".join(sql.split()).upper()
            if normalized.startswith("LOCK TABLE") or (
                "SELECT ID, COUNT(*) OVER ()" in normalized
            ) or normalized.startswith("CREATE FUNCTION") or normalized.startswith(
                "CREATE CONSTRAINT TRIGGER"
            ):
                observed.append((normalized, connection.in_atomic_block))
            return execute(sql, params, many, context)

        with _rollback_migration_work(self):
            with connection.execute_wrapper(capture):
                MigrationExecutor(connection).migrate([PREVIOUS])
                self.assertFalse(_migration_is_recorded())
                _assert_applied_core_head(self, PREVIOUS[1])
                previous_trigger_count = _trigger_count()
                self.assertEqual(
                    previous_trigger_count,
                    isolation_state.REQUIRED_TRIGGER_COUNT[PREVIOUS[1]],
                )
                ensure_test_policy_text()
                grant = _seed_beta_entitlement(route, effective_at=database_now())
                event = OrganismEntitlementEvent.objects.get(
                    entitlement=grant,
                    sequence=1,
                    event_type="GRANTED",
                )
                before_forward = _records_state()
                MigrationExecutor(connection).migrate([MIGRATION])
                self.assertTrue(_migration_is_recorded())
                _assert_applied_core_head(self, MIGRATION[1])
                self.assertEqual(_records_state(), before_forward)
                self.assertEqual(
                    _trigger_count(),
                    isolation_state.REQUIRED_TRIGGER_COUNT[MIGRATION[1]],
                )
                self.assertEqual(_function_count(), 1)
                _assert_guard_present(self)
                self.assertEqual(
                    [sql.split(" ", 2)[0] for sql, _ in observed],
                    ["LOCK", "SELECT", "CREATE", "CREATE"],
                )
                self.assertTrue(all(in_atomic for _, in_atomic in observed))
                self.assertEqual(
                    OrganismEntitlementEvent.objects.get(pk=event.pk).event_uuid,
                    event.event_uuid,
                )

                with self.assertRaisesRegex(RuntimeError, "reverse refused"):
                    MigrationExecutor(connection).migrate([PREVIOUS])
                self.assertTrue(_migration_is_recorded())
                _assert_applied_core_head(self, MIGRATION[1])
                self.assertEqual(_records_state(), before_forward)
                _assert_guard_present(self)

        self.assertEqual(_records_state(), before)
        self.assertEqual(_trigger_count(), original_trigger_count)
        _assert_guard_present(self)

    def test_w_se_k3_2_g_existing_obstruction_refuses_without_partial_install(self):
        # H100 chunk 4 tests v1.4 (R-1): the entitlement fixtures need only an organism with an LO
        # event. A route with no gateway effect (FOUND and one phase advance, neither an admitted
        # entry) leaves no core_commandintake row, so the migration to 0047 passes 0049's reverse,
        # which refuses while intake rows exist (the table refuses DELETE and TRUNCATE).
        route = prepare_route(
            tag=f"k3-migration-obstructed-{uuid4().hex}",
            governance_enabled=None,
            include_settings=False,
            include_question=False,
            to_birthing_phase=False,
        )
        self.assertFalse(CommandIntake.objects.exists())
        before = _records_state()
        original_trigger_count = _trigger_count()

        with _rollback_migration_work(self):
            MigrationExecutor(connection).migrate([PREVIOUS])
            previous_trigger_count = _trigger_count()
            self.assertEqual(
                previous_trigger_count,
                isolation_state.REQUIRED_TRIGGER_COUNT[PREVIOUS[1]],
            )
            row = _create_eventless_beta(route)
            before_forward = _records_state()
            with self.assertRaisesRegex(RuntimeError, "exactly one first GRANTED event") as obstruction:
                MigrationExecutor(connection).migrate([MIGRATION])

            self.assertIn(str(row.pk), str(obstruction.exception))
            self.assertFalse(_migration_is_recorded())
            _assert_applied_core_head(self, PREVIOUS[1])
            self.assertEqual(_records_state(), before_forward)
            self.assertEqual(
                _trigger_count(),
                isolation_state.REQUIRED_TRIGGER_COUNT[PREVIOUS[1]],
            )
            _assert_guard_absent(self)

        self.assertEqual(_records_state(), before)
        self.assertEqual(_trigger_count(), original_trigger_count)
        _assert_guard_present(self)

    def test_w_se_k3_2_h_empty_reverse_and_forward_restore_the_guard(self):
        original_trigger_count = _trigger_count()
        with _rollback_migration_work(self):
            MigrationExecutor(connection).migrate([PREVIOUS])
            self.assertFalse(_migration_is_recorded())
            self.assertEqual(
                _trigger_count(),
                isolation_state.REQUIRED_TRIGGER_COUNT[PREVIOUS[1]],
            )
            _assert_guard_absent(self)

            MigrationExecutor(connection).migrate([MIGRATION])
            self.assertTrue(_migration_is_recorded())
            self.assertEqual(
                _trigger_count(),
                isolation_state.REQUIRED_TRIGGER_COUNT[MIGRATION[1]],
            )
            _assert_guard_present(self)


class SEGrantedGuardBehaviorTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True
    reset_sequences = True

    def test_w_se_k3_1_composed_grant_commits_with_first_granted_event(self):
        route = prepare_route(tag=f"k3-valid-{uuid4().hex}")
        before = _records_state()
        with transaction.atomic():
            grant = _seed_beta_entitlement(route, effective_at=database_now())
        event = OrganismEntitlementEvent.objects.get(
            entitlement=grant,
            sequence=1,
            event_type="GRANTED",
        )
        self.assertEqual(event.entry_mode, "COMPOSED")
        self.assertIsNone(event.predecessor)
        after = _records_state()
        self.assertEqual(len(after[1]), len(before[1]) + 1)
        self.assertEqual(len(after[2]), len(before[2]) + 1)
        self.assertEqual(
            OrganismEntitlement.objects.get(pk=grant.pk).entitlement_uuid,
            grant.entitlement_uuid,
        )
        self.assertEqual(
            OrganismEntitlementEvent.objects.get(pk=event.pk).event_uuid,
            event.event_uuid,
        )
        _assert_guard_present(self)

    def test_w_se_k3_1_missing_or_immediate_first_event_aborts_the_insert(self):
        route = prepare_route(tag=f"k3-missing-{uuid4().hex}")
        before = _records_state()

        with self.assertRaises(IntegrityError) as deferred_error:
            with transaction.atomic():
                _create_eventless_beta(route)
        self.assertTrue(_is_entitlement_granted_guard_failure(deferred_error.exception))
        self._assert_sqlstate_message(deferred_error.exception)
        self.assertEqual(_records_state(), before)

        route = prepare_route(tag=f"k3-immediate-{uuid4().hex}")
        before = _records_state()
        with self.assertRaises(IntegrityError) as immediate_error:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                _create_eventless_beta(route)
        self.assertTrue(_is_entitlement_granted_guard_failure(immediate_error.exception))
        self._assert_sqlstate_message(immediate_error.exception)
        self.assertEqual(_records_state(), before)

    def _assert_sqlstate_message(self, error):
        cause = error.__cause__
        while cause is not None:
            state = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
            diagnostic = getattr(cause, "diag", None)
            if state == "23000":
                self.assertEqual(
                    getattr(diagnostic, "message_primary", None),
                    "SE_ENTITLEMENT_WITHOUT_GRANT",
                )
                return
            cause = cause.__cause__
        self.fail("the database failure did not expose SQLSTATE 23000")

    def test_w_se_k3_3_opt_in_translates_only_the_guard_failure_after_rollback(self):
        route = prepare_route(tag=f"k3-opt-in-translation-{uuid4().hex}")
        ensure_test_policy_text()
        route.birthing_service.birth_living_organism(
            _birthing_command(route, offering_choice="FREE", suffix="k3-free")
        )
        before = _records_state()
        service = _EventlessComposer(route)
        command = _election_command(route, suffix="k3-guard")
        with self.assertRaises(S015EntitlementTechnicalFailure) as failure:
            service.activate_beta(command)
        self.assertEqual(str(failure.exception), T1)
        self.assertIsInstance(failure.exception.__cause__, IntegrityError)
        self.assertTrue(_is_entitlement_granted_guard_failure(failure.exception.__cause__))
        self.assertEqual(_records_state(), before)

    def test_w_se_k3_4_birthing_translates_only_the_guard_failure_after_rollback(self):
        route = prepare_route(tag=f"k3-birth-translation-{uuid4().hex}")
        ensure_test_policy_text()
        before = _records_state()
        route.birthing_service._entitlement_service = _EventlessComposer(route)
        with self.assertRaises(S015BirthingTechnicalFailure) as failure:
            route.birthing_service.birth_living_organism(
                _birthing_command(
                    route,
                    offering_choice="COMPLIMENTARY_BETA",
                    suffix="k3-guard",
                )
            )
        self.assertEqual(str(failure.exception), BIRTHING_FAILURE)
        self.assertIsInstance(failure.exception.__cause__, IntegrityError)
        self.assertTrue(_is_entitlement_granted_guard_failure(failure.exception.__cause__))
        self.assertEqual(_records_state(), before)

    def test_w_se_k3_5_unrelated_integrity_errors_at_commit_propagate_on_both_routes(self):
        route = prepare_route(tag=f"k3-opt-in-unrelated-{uuid4().hex}")
        ensure_test_policy_text()
        route.birthing_service.birth_living_organism(
            _birthing_command(route, offering_choice="FREE", suffix="k3-free")
        )
        before = _records_state()
        with _deferred_unrelated_error():
            service = OrganismEntitlementService(
                authority=route.birthing_service._authority,
                clock=database_now,
            )
            with self.assertRaises(IntegrityError) as opt_in_error:
                service.activate_beta(_election_command(route, suffix="k3-unrelated"))
        self._assert_unrelated_integrity(opt_in_error.exception)
        self.assertEqual(_records_state(), before)

        route = prepare_route(tag=f"k3-birth-unrelated-{uuid4().hex}")
        ensure_test_policy_text()
        before = _records_state()
        with _deferred_unrelated_error():
            with self.assertRaises(IntegrityError) as birthing_error:
                route.birthing_service.birth_living_organism(
                    _birthing_command(
                        route,
                        offering_choice="COMPLIMENTARY_BETA",
                        suffix="k3-unrelated",
                    )
                )
        self._assert_unrelated_integrity(birthing_error.exception)
        self.assertEqual(_records_state(), before)

    def _assert_unrelated_integrity(self, error):
        self.assertFalse(_is_entitlement_granted_guard_failure(error))
        cause = error.__cause__
        self.assertIsNotNone(cause)
        self.assertEqual(
            getattr(getattr(cause, "diag", None), "message_primary", None),
            "SE_K3_UNRELATED_COMMIT",
        )

    def test_w_se_k3_6_a_b_bypass_commits_and_records_exact_declaration(self):
        route = prepare_route(tag=f"k3-bypass-{uuid4().hex}")
        reason = "W-K3.6(a): explicitly event-less fixture for guard boundary"
        with declared_event_less_entitlement_rows(reason):
            row = _create_eventless_beta(route)
        persisted = OrganismEntitlement.objects.get(pk=row.pk)
        self.assertEqual(persisted.living_organism_id, route.organism.pk)
        self.assertEqual(persisted.basis, "BETA_OPT_IN")
        self.assertIn((self.id(), reason), EVENT_LESS_ENTITLEMENT_DECLARATIONS)
        _assert_guard_present(self)

    def test_w_se_k3_6_c_database_error_rolls_back_bypass_and_guard_state(self):
        route = prepare_route(tag=f"k3-bypass-db-error-{uuid4().hex}")
        before = _records_state()
        fixture_rows = []
        with self.assertRaises(DatabaseError) as original:
            with declared_event_less_entitlement_rows(
                "W-K3.6(c): preserve row validation while bypassing grant guard"
            ):
                fixture_rows.append(_create_eventless_beta(route))
                _create_eventless_beta(route, limits=[], use_bulk_insert=True)
        self.assertIn("SE_ENTITLEMENT_INVALID", str(original.exception))
        self.assertNotIn("current transaction is aborted", str(original.exception).lower())
        self.assertEqual(_records_state(), before)
        self.assertEqual(len(fixture_rows), 1)
        self.assertFalse(
            OrganismEntitlement.objects.filter(pk=fixture_rows[0].pk).exists()
        )
        _assert_guard_present(self)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone()[0], 1)

        with self.assertRaises(IntegrityError) as bare:
            with transaction.atomic():
                _create_eventless_beta(route)
        self.assertTrue(_is_entitlement_granted_guard_failure(bare.exception))
        _assert_guard_present(self)

    def test_w_se_k3_6_d_non_database_error_rolls_back_bypass_and_guard_state(self):
        route = prepare_route(tag=f"k3-bypass-python-error-{uuid4().hex}")
        before = _records_state()
        error = ValueError("fixture failure")
        fixture_rows = []
        with self.assertRaises(ValueError) as raised:
            with declared_event_less_entitlement_rows(
                "W-K3.6(d): restore guard after a non-database exception"
            ):
                fixture_rows.append(_create_eventless_beta(route))
                raise error
        self.assertIs(raised.exception, error)
        self.assertEqual(_records_state(), before)
        self.assertEqual(len(fixture_rows), 1)
        self.assertFalse(
            OrganismEntitlement.objects.filter(pk=fixture_rows[0].pk).exists()
        )
        _assert_guard_present(self)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone()[0], 1)
        with self.assertRaises(IntegrityError) as bare:
            with transaction.atomic():
                _create_eventless_beta(route)
        self.assertTrue(_is_entitlement_granted_guard_failure(bare.exception))

    def test_w_se_k3_6_e_blank_reason_fails_before_sql(self):
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaisesRegex(ValueError, "needs a reason"):
                with declared_event_less_entitlement_rows(" \t\n "):
                    self.fail("blank declaration unexpectedly entered its context")
        self.assertEqual(queries.captured_queries, [])
        _assert_guard_present(self)


@contextmanager
def _deferred_unrelated_error():
    suffix = uuid4().hex
    function = f"s015_sek3_unrelated_{suffix}"
    trigger = f"s015_sek3_unrelated_{suffix}"
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE FUNCTION public.{function}()
            RETURNS trigger LANGUAGE plpgsql
            AS $body$
            BEGIN
                RAISE EXCEPTION 'SE_K3_UNRELATED_COMMIT'
                    USING ERRCODE = 'integrity_constraint_violation';
            END
            $body$
            """
        )
        cursor.execute(
            f"""
            CREATE CONSTRAINT TRIGGER {trigger}
            AFTER INSERT ON public.core_organismentitlement
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.{function}()
            """
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                f"DROP TRIGGER IF EXISTS {trigger} "
                "ON public.core_organismentitlement"
            )
            cursor.execute(f"DROP FUNCTION IF EXISTS public.{function}()")


class SEGrantedGuardMatcherTests(SimpleTestCase):
    class _Cause(Exception):
        def __init__(self, sqlstate, message):
            self.sqlstate = sqlstate
            self.diag = SimpleNamespace(message_primary=message)

    @staticmethod
    def _error(cause):
        error = IntegrityError("outer integrity error")
        error.__cause__ = cause
        return error

    def test_w_se_k3_5_matcher_requires_the_exact_pair_on_one_cause(self):
        exact = self._Cause("23000", "SE_ENTITLEMENT_WITHOUT_GRANT")
        wrong_message = self._Cause("23000", "SE_ENTITLEMENT_WITHOUT_GRANT extra")
        wrong_state = self._Cause("23505", "SE_ENTITLEMENT_WITHOUT_GRANT")
        split_state = self._Cause("23000", "other")
        split_state.__cause__ = self._Cause(None, "SE_ENTITLEMENT_WITHOUT_GRANT")

        self.assertTrue(_is_entitlement_granted_guard_failure(self._error(exact)))
        self.assertFalse(
            _is_entitlement_granted_guard_failure(self._error(wrong_message))
        )
        self.assertFalse(_is_entitlement_granted_guard_failure(self._error(wrong_state)))
        self.assertFalse(_is_entitlement_granted_guard_failure(self._error(split_state)))


class SEEntitlementBypassRestorationTests(SimpleTestCase):
    class _Cursor:
        def __init__(self, restoration_error):
            self.restoration_error = restoration_error
            self.statements = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, sql, params=None):
            statement = " ".join(sql.split())
            self.statements.append(statement)
            if "ENABLE TRIGGER" in statement:
                raise self.restoration_error

        def fetchone(self):
            return ("test_owner", "test_owner")

    class _Connection:
        in_atomic_block = False

        def __init__(self, cursor):
            self._cursor = cursor

        def get_autocommit(self):
            return True

        def cursor(self):
            return self._cursor

    def _run_with_restoration_failure(self, fixture_error):
        restoration_error = RuntimeError("injected ENABLE failure")
        cursor = self._Cursor(restoration_error)
        active_connection = self._Connection(cursor)
        with (
            patch(
                "tests.se_route_support.connections",
                {"default": active_connection},
            ),
            patch(
                "tests.se_route_support.transaction.atomic",
                side_effect=lambda *, using: nullcontext(),
            ),
            patch("tests.se_route_support.EVENT_LESS_ENTITLEMENT_DECLARATIONS", []),
        ):
            with self.assertRaises(
                EventLessEntitlementRestorationError
            ) as raised:
                with declared_event_less_entitlement_rows(
                    "database-free restoration fault injection"
                ):
                    if fixture_error is not None:
                        raise fixture_error
        return raised.exception, restoration_error, cursor.statements

    def test_successful_fixture_reports_restoration_failure(self):
        failure, restoration_error, statements = (
            self._run_with_restoration_failure(None)
        )
        self.assertIsNone(failure.fixture_error)
        self.assertIs(failure.restoration_error, restoration_error)
        self.assertIs(failure.__cause__, restoration_error)
        self.assertIn("database alias 'default'", str(failure))
        self.assertIn("s015_organismentitlement_granted_guard", str(failure))
        self.assertTrue(any("DISABLE TRIGGER" in sql for sql in statements))
        self.assertTrue(any("ENABLE TRIGGER" in sql for sql in statements))

    def test_fixture_and_restoration_failures_are_both_retained(self):
        fixture_error = ValueError("injected fixture failure")
        failure, restoration_error, statements = (
            self._run_with_restoration_failure(fixture_error)
        )
        self.assertIs(failure.fixture_error, fixture_error)
        self.assertIs(failure.restoration_error, restoration_error)
        self.assertIs(failure.__cause__, restoration_error)
        self.assertTrue(any("DISABLE TRIGGER" in sql for sql in statements))
        self.assertTrue(any("ENABLE TRIGGER" in sql for sql in statements))
