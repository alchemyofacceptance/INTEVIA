from __future__ import annotations

import json
import uuid

from django.contrib.auth.models import User
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase
from unittest import skipUnless

from core.identity import canonical_username_v1
from core.models import Identity


# Corrections under Human Governor Option 1 (UFUND-2, 16 Sep 2026), each citing designated design
# LD_RETURN_PKT_A_3_MIGRATION_0022_DESIGN_v0_6.md (sha256 59a07477...):
#   C-1 section 5.5 - core_identityresolution carries credential_link; inserts supply it from the identity's own
#       credential, and the schema check expects the four design columns.
#   C-2 section 5.4 and U-14 - the L2 body canonical form is undefined and outside this bound, so
#       s015_0022_l2_preimage refuses every body; the test asserts that specific refusal for object and non-object bodies.
#   C-3 section 5.5 - no guardian refuses DELETE on the resolution table (the takedown is that delete); the rollback test
#       rolls back deliberately after the delete and asserts the recorded severance does not survive.
# This file was not collected before UFUND-2 Change B (finding F-B1) and its expectations predated the design (F-B2).
U14_REFUSAL = "S015 U-14: L2 body canonical form is undefined and outside this bound"

POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "S015 0022 contract checks are PostgreSQL-only",
)


@POSTGRESQL_ONLY
class S0150022ContractTests(TransactionTestCase):
    reset_sequences = True

    def _identity(self, label: str) -> Identity:
        username = f"{label}_{uuid.uuid4().hex[:12]}"
        user = User.objects.create_user(username=username, password="password123")
        return Identity.objects.create(
            credential=user,
            canonical_username=canonical_username_v1(username),
            display_name=f"{label} identity",
        )

    def _insert_resolution(self, identity: Identity, *, display_name: str | None = None) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.core_identityresolution (
                    identity_id, display_name, canonical_username, credential_link
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    identity.pk,
                    display_name or identity.display_name or "",
                    identity.canonical_username,
                    identity.credential_id,  # C-1: design v0.6 section 5.5
                ),
            )

    def _resolution_count(self, identity_id: int) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.core_identityresolution WHERE identity_id = %s",
                [identity_id],
            )
            return cursor.fetchone()[0]

    def _severed_count(self, identity_id: int) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.core_identityresolutionsevered WHERE identity_id = %s",
                [identity_id],
            )
            return cursor.fetchone()[0]

    def test_lawful_resolution_insert_commits(self):
        identity = self._identity("lawful")
        self._insert_resolution(identity)

        self.assertEqual(self._resolution_count(identity.pk), 1)

    def test_takedown_records_severance_atomically(self):
        identity = self._identity("takedown")
        self._insert_resolution(identity)

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM public.core_identityresolution WHERE identity_id = %s",
                    [identity.pk],
                )

        self.assertEqual(self._resolution_count(identity.pk), 0)
        self.assertEqual(self._severed_count(identity.pk), 1)

    def test_reinsert_after_severance_is_refused(self):
        identity = self._identity("reinsert")
        self._insert_resolution(identity)

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM public.core_identityresolution WHERE identity_id = %s",
                    [identity.pk],
                )

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                self._insert_resolution(identity, display_name="new name")

        self.assertEqual(self._resolution_count(identity.pk), 0)
        self.assertEqual(self._severed_count(identity.pk), 1)

    def test_resolution_rollback_leaves_no_severance_trace(self):
        # C-3: design v0.6 section 5.5 - "No guardian refuses DELETE on the resolution table - the takedown is that delete."
        # The delete is not expected to raise. The transaction is rolled back deliberately after it, and the severance the
        # delete recorded inside the transaction must not survive the rollback.
        class RollbackProbe(Exception):
            pass

        identity = self._identity("rollback")
        self._insert_resolution(identity)

        with self.assertRaises(RollbackProbe):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM public.core_identityresolution WHERE identity_id = %s",
                        [identity.pk],
                    )
                self.assertEqual(self._severed_count(identity.pk), 1)
                raise RollbackProbe()

        self.assertEqual(self._resolution_count(identity.pk), 1)
        self.assertEqual(self._severed_count(identity.pk), 0)

    def test_l2_preimage_refuses_every_body_while_u14_stands(self):
        # C-2: design v0.6 section 5.4 and U-14. The L2 body grammar is undefined, so the function is named, not specified,
        # and refuses object and non-object bodies alike with the U-14 refusal. Only that refusal satisfies this test.
        # (A comment, not a docstring: the verbose runner prints a docstring as an extra line, which the shared route's
        # result parser does not recognise - run CHB_20260916T210412Z.)
        for label, body in (("non-object body", [1, 2, 3]), ("object body", {"alpha": 1, "beta": [2, 3]})):
            with self.subTest(body=label):
                with self.assertRaises(DatabaseError) as raised:
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "SELECT public.s015_0022_l2_preimage(%s::bytea, %s::jsonb)",
                                [b"z" * 32, json.dumps(body)],
                            )
                self.assertIn(U14_REFUSAL, str(raised.exception))

    def test_final_schema_objects_and_trigger_names_exist(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relname, array_agg(a.attname ORDER BY a.attnum)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                WHERE c.relname IN (
                    'core_identityresolution',
                    'core_identityresolutionsevered'
                )
                GROUP BY c.relname
                ORDER BY c.relname
                """
            )
            tables = {name: columns for name, columns in cursor.fetchall()}

        self.assertEqual(
            tables["core_identityresolution"],
            ["identity_id", "display_name", "canonical_username", "credential_link"],  # C-1: design v0.6 section 5.5
        )
        self.assertEqual(
            tables["core_identityresolutionsevered"],
            ["identity_id", "severed_at"],
        )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT t.tgname
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                WHERE c.relname = 'core_identityresolutionsevered'
                  AND NOT t.tgisinternal
                ORDER BY t.tgname
                """
            )
            trigger_names = [row[0] for row in cursor.fetchall()]

        self.assertEqual(
            trigger_names,
            [
                "s015_0022_severed_delete_append_only",
                "s015_0022_severed_truncate_append_only",
                "s015_0022_severed_update_append_only",
            ],
        )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT t.tgname, t.tgtype
                FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                WHERE c.relname = 'core_identity' AND t.tgname = 's015_0022_guard_identity_resolution'
                """
            )
            row = cursor.fetchone()

        # Existence and timing only. The guardian's body is a no-op (0022 IDENTITY_GUARD_BODY; ILC Datacron: not delivered;
        # kept under PKT-B by the Q-F1 ruling). Passing this test does not establish the insert-only protection.
        self.assertIsNotNone(row)
        self.assertTrue(row[1] & 4)
        self.assertFalse(row[1] & 16)