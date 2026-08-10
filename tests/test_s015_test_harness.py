import uuid
from unittest import mock, skipUnless

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase

from intevia.test_postgresql_backend.operations import (
    DatabaseOperations,
    S015_TEST_ROUTE,
    S015_TRUNCATE_GUARDIANS,
)


class S015TestBackendQualificationTests(SimpleTestCase):
    def test_backend_refuses_connected_database_name_mismatch(self):
        fake_connection = mock.MagicMock()
        fake_connection.settings_dict = {
            "S015_TEST_ROUTE": S015_TEST_ROUTE,
            "NAME": "test_intevia_living_organism_expected",
        }
        fake_cursor = fake_connection.cursor.return_value.__enter__.return_value
        fake_cursor.fetchone.return_value = (
            "test_intevia_living_organism_unqualified",
        )

        operations = DatabaseOperations(fake_connection)

        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "S015 test lifecycle backend refuses the connected database",
        ):
            operations._qualified_database_name()


@skipUnless(connection.vendor == "postgresql", "PostgreSQL lifecycle control")
class S015PostgreSQLFlushLifecycleTests(TransactionTestCase):
    reset_sequences = True

    def guardian_states(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT relation.relname, guardian.tgname, guardian.tgenabled
                FROM pg_trigger guardian
                JOIN pg_class relation ON relation.oid = guardian.tgrelid
                WHERE guardian.tgname = ANY(%s)
                ORDER BY relation.relname
                """,
                [list(S015_TRUNCATE_GUARDIANS.values())],
            )
            return cursor.fetchall()

    def test_flush_restores_every_s015_truncate_guardian(self):
        call_command("flush", interactive=False, verbosity=0)

        states = self.guardian_states()

        self.assertEqual(len(states), len(S015_TRUNCATE_GUARDIANS))
        self.assertTrue(all(enabled == "O" for _, _, enabled in states))

    def test_direct_mutations_remain_refused_outside_flush(self):
        principal_uuid = str(uuid.uuid4())
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO core_authorityprincipal (
                    principal_uuid,
                    canonical_governed_source_id,
                    governed_source_namespace,
                    display_label,
                    bootstrap_invocation_fingerprint,
                    recorded_at
                ) VALUES (%s, %s, %s, %s, NULL, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                [
                    principal_uuid,
                    f"urn:intevia:test:{principal_uuid}",
                    "S015_TEST_LIFECYCLE",
                    "Lifecycle guardian control",
                ],
            )
            principal_id = cursor.fetchone()[0]

        guarded_statements = (
            (
                "UPDATE core_authorityprincipal "
                "SET canonical_governed_source_id = %s WHERE id = %s",
                [f"urn:intevia:test:changed:{principal_uuid}", principal_id],
            ),
            ("DELETE FROM core_authorityprincipal WHERE id = %s", [principal_id]),
            ("TRUNCATE TABLE core_authorityprincipal CASCADE", []),
        )
        for sql, parameters in guarded_statements:
            with self.assertRaises(IntegrityError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql, parameters)