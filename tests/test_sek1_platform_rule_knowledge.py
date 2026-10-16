"""Test statement-start knowledge for governed platform rules.

These controlled two-connection witnesses implement PKT-SE-K1 build commission
v1.1 and HG_RULING_O1_O3_AND_PKT_CLK_1_20261014, O-3 option (b). Under READ
COMMITTED, the checking transaction opens before the platform rule commits, and
the rule commits before the checking statement runs. The future-effective case
shows that a visible rule is not thereby applicable.
"""

import importlib
import json
from threading import Event, Thread
from unittest import skipUnless
from uuid import UUID

from django.db import close_old_connections, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from src.intevia.services.organism_entitlement_service import check_allowance
from tests.sb2_route_support import birth, prepare_route


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "SEK1 statement-start witnesses require PostgreSQL",
)
FREE_RULE_UUID = UUID("c81640de-f776-5b31-a046-0046e0000002")
SE_MIGRATION = ("core", "0046_se_entitlement_foundation")
SEK1_MIGRATION = ("core", "0047_sek1_platform_rule_statement_start")


@POSTGRESQL_ONLY
class SEK1PlatformRuleKnowledgeTests(TransactionTestCase):
    def _record_free_tier_version(self, limit, *, future):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT statement_timestamp(), "
                "statement_timestamp() - INTERVAL '1 second', "
                "statement_timestamp() - INTERVAL '2 seconds', "
                "statement_timestamp() + INTERVAL '1 day'"
            )
            database_now, past_effective, past_decided, future_effective = (
                cursor.fetchone()
            )
            cursor.execute(
                "SELECT body FROM public.core_governedrule "
                "WHERE rule_uuid = %s AND version = 1",
                [FREE_RULE_UUID],
            )
            raw_body = cursor.fetchone()[0]
            body = (
                json.loads(raw_body)
                if isinstance(raw_body, str)
                else json.loads(json.dumps(raw_body))
            )
            self.assertIsInstance(body, dict)
            body["limits"]["circles"] = {"kind": "LIMIT", "value": limit}
            effective_from = future_effective if future else past_effective
            decided_at = database_now if future else past_decided
            cursor.execute(
                """
                INSERT INTO public.core_governedrule (
                    rule_uuid, version, scope_kind, scope_organism_id,
                    scope_circle_id, code, body, text_commitment,
                    authority_reference, source, effective_from, decided_at,
                    observations, enterer_identity_id, recorded_at
                ) VALUES (
                    %s, 2, 'PLATFORM', NULL, NULL, 'FREE_TIER', %s::jsonb,
                    NULL, 'test-fixture:sek1-platform-rule-v2',
                    'PLATFORM_RULING', %s, %s, NULL, NULL, NULL
                )
                """,
                [FREE_RULE_UUID, json.dumps(body), effective_from, decided_at],
            )

    def _run_ordered_race(self, *, new_limit, future=False):
        transaction_started = Event()
        rule_committed = Event()
        outcomes = []
        route = prepare_route()
        birth(route)
        organism_uuid = route.organism.organism_id

        def evaluate_in_open_transaction():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connections["default"].cursor() as cursor:
                        cursor.execute(
                            "SELECT transaction_timestamp(), "
                            "current_setting('transaction_isolation')"
                        )
                        transaction_start, isolation = cursor.fetchone()
                    transaction_started.set()
                    if not rule_committed.wait(timeout=10):
                        raise AssertionError("platform rule was not committed")
                    with connections["default"].cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT
                                (
                                    SELECT (rule.body->'limits'->'circles'->>'value')::bigint
                                      FROM public.core_governedrule rule
                                     WHERE rule.rule_uuid = %s
                                       AND rule.code = 'FREE_TIER'
                                       AND rule.effective_from <= statement_timestamp()
                                       AND rule.recorded_at <= statement_timestamp()
                                     ORDER BY rule.effective_from DESC,
                                              rule.recorded_at DESC,
                                              rule.version DESC,
                                              rule.id DESC
                                     LIMIT 1
                                ),
                                (
                                    SELECT (rule.body->'limits'->'circles'->>'value')::bigint
                                      FROM public.core_governedrule rule
                                     WHERE rule.rule_uuid = %s
                                       AND rule.code = 'FREE_TIER'
                                       AND rule.effective_from <= statement_timestamp()
                                       AND rule.recorded_at <= transaction_timestamp()
                                     ORDER BY rule.effective_from DESC,
                                              rule.recorded_at DESC,
                                              rule.version DESC,
                                              rule.id DESC
                                     LIMIT 1
                                ),
                                statement_timestamp()
                            """,
                            [FREE_RULE_UUID, FREE_RULE_UUID],
                        )
                        statement_answer, transaction_answer, at = cursor.fetchone()
                    allowance = check_allowance(
                        organism_uuid,
                        "circles",
                        10,
                        at,
                    )
                    outcomes.append(
                        {
                            "transaction_start": transaction_start,
                            "isolation": isolation,
                            "statement_answer": statement_answer,
                            "transaction_answer": transaction_answer,
                            "function_answer": allowance.limit,
                            "status": allowance.status,
                        }
                    )
            except Exception as error:
                outcomes.append(error)
                transaction_started.set()
            finally:
                close_old_connections()

        worker = Thread(target=evaluate_in_open_transaction)
        worker.start()
        if not transaction_started.wait(timeout=10):
            rule_committed.set()
            worker.join(timeout=10)
            self.fail("checking transaction did not start")
        try:
            if not outcomes:
                self._record_free_tier_version(new_limit, future=future)
        finally:
            rule_committed.set()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "checking transaction did not finish")
        self.assertEqual(len(outcomes), 1)
        if isinstance(outcomes[0], Exception):
            raise outcomes[0]
        return outcomes[0]

    def _assert_race(self, *, new_limit, expected_statement, expected_transaction, future=False):
        outcome = self._run_ordered_race(new_limit=new_limit, future=future)
        self.assertEqual(outcome["isolation"], "read committed")
        self.assertIsNotNone(outcome["transaction_start"])
        if future:
            self.assertEqual(outcome["statement_answer"], expected_statement)
            self.assertEqual(outcome["transaction_answer"], expected_transaction)
        else:
            self.assertNotEqual(
                outcome["statement_answer"], outcome["transaction_answer"]
            )
            self.assertEqual(outcome["statement_answer"], expected_statement)
            self.assertEqual(outcome["transaction_answer"], expected_transaction)
        self.assertEqual(outcome["function_answer"], expected_statement)
        self.assertEqual(
            outcome["status"],
            "within" if expected_statement >= 10 else "exceeds",
        )

    def test_platform_rule_increase_visible_after_transaction_start(self):
        self._assert_race(
            new_limit=12,
            expected_statement=12,
            expected_transaction=9,
        )

    def test_platform_rule_reduction_visible_after_transaction_start(self):
        self._assert_race(
            new_limit=5,
            expected_statement=5,
            expected_transaction=9,
        )

    def test_future_platform_rule_is_not_applied_before_effective_from(self):
        self._assert_race(
            new_limit=12,
            expected_statement=9,
            expected_transaction=9,
            future=True,
        )


@POSTGRESQL_ONLY
class SEK1MigrationTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def _assert_function(self, expected_body):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.prosrc, p.provolatile, p.proisstrict, p.pronargs,
                       oidvectortypes(p.proargtypes), pg_get_function_result(p.oid)
                  FROM pg_proc p
                 WHERE p.oid =
                       'public.s015_entitlement_limit(uuid,text,timestamptz)'::regprocedure
                """
            )
            body, volatility, is_strict, arg_count, arg_types, result = (
                cursor.fetchone()
            )
        self.assertEqual(body, expected_body)
        self.assertEqual(volatility, "s")
        self.assertTrue(is_strict)
        self.assertEqual(arg_count, 3)
        self.assertEqual(arg_types, "uuid, text, timestamp with time zone")
        self.assertEqual(result, "TABLE(kind text, value bigint)")

    def test_0047_preserves_declaration_and_reverse_restores_0046_body(self):
        se_migration = importlib.import_module(
            "core.migrations.0046_se_entitlement_foundation"
        )
        sek1_migration = importlib.import_module(
            "core.migrations.0047_sek1_platform_rule_statement_start"
        )
        old_body = dict(se_migration.SE_FUNCTION_BODIES)[
            "s015_entitlement_limit"
        ]
        self.addCleanup(
            lambda: MigrationExecutor(connection).migrate([SEK1_MIGRATION])
        )

        MigrationExecutor(connection).migrate([SE_MIGRATION])
        self._assert_function(old_body)

        MigrationExecutor(connection).migrate([SEK1_MIGRATION])
        self._assert_function(sek1_migration._ENTITLEMENT_LIMIT_BODY)
