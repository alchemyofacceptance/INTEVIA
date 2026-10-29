import io
import unittest
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

from tests.s015_0021_support import (
    migrate_to_target_with_restore,
    restore_migration_targets,
)


class P1MigrationRestoreTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def setUp(self):
        self.received_targets = tuple(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )
        self.addCleanup(
            restore_migration_targets, connection, self.received_targets
        )

    def run_minimal_migration_case(self, fail_in_setup):
        should_fail_in_setup = fail_in_setup

        class MinimalMigrationCase(unittest.TestCase):
            fail_in_setup = should_fail_in_setup

            def setUp(self):
                migrate_to_target_with_restore(
                    self, ("core", "0042_sb1_founding_terms_phases")
                )
                if self.fail_in_setup:
                    raise RuntimeError("intentional setup failure")

            def test_earlier_migration_is_active(self):
                self.assertNotIn(
                    "core_learnerregistration",
                    connection.introspection.table_names(),
                )
                self.assertNotIn(
                    "core_registrationreceipt",
                    connection.introspection.table_names(),
                )

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(
            MinimalMigrationCase
        )
        output = io.StringIO()
        result = unittest.TextTestRunner(stream=output).run(suite)
        return result, output.getvalue()

    def assert_original_migrations_restored(self):
        applied = MigrationRecorder(connection).applied_migrations()
        self.assertTrue(set(self.received_targets).issubset(applied))
        self.assertIn(
            ("core", "0043_p1_governed_registration"),
            applied,
        )
        self.assertIn(
            "core_learnerregistration",
            connection.introspection.table_names(),
        )
        self.assertIn(
            "core_registrationreceipt",
            connection.introspection.table_names(),
        )

    def test_setup_failure_restores_received_migrations(self):
        with patch(
            "tests.s015_0021_support.restore_migration_targets",
            wraps=restore_migration_targets,
        ) as restore:
            result, _ = self.run_minimal_migration_case(fail_in_setup=True)

        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("intentional setup failure", result.errors[0][1])
        self.assertEqual(restore.call_count, 1)
        self.assert_original_migrations_restored()

    def test_ordinary_case_restores_received_migrations_once(self):
        with patch(
            "tests.s015_0021_support.restore_migration_targets",
            wraps=restore_migration_targets,
        ) as restore:
            result, output = self.run_minimal_migration_case(fail_in_setup=False)

        self.assertEqual(result.testsRun, 1)
        self.assertTrue(result.wasSuccessful(), output)
        self.assertEqual(restore.call_count, 1)
        self.assert_original_migrations_restored()
