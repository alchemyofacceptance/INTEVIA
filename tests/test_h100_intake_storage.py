"""Witness the H100 intake table, append-only triggers, and database-owned time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import import_module
from threading import Event
from queue import Queue
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, connections, transaction
from django.test import TransactionTestCase

from core.models import CommandIntake, Identity, IntakeEntry
from core.organism_contract import OrganismAction
from src.intevia.services import command_intake
from src.intevia.services.command_intake import ActorRef, IntakeShape, admit_request
from tests.s015_concurrency_support import cleanup_workers, start_worker
from tests.s015_0021_support import create_test_identity, current_applied_core_head
from verification.isolation.state import REQUIRED_TRIGGER_COUNT


ACTION = OrganismAction.TRANSITION_LIVING_ORGANISM.value


def _actor_ref(username: str) -> ActorRef:
    identity = create_test_identity(
        username=username,
        access_state=Identity.AccessState.ACTIVE,
    )
    return ActorRef(
        pk=identity.pk,
        identity_id=identity.identity_id,
        access_epoch=identity.access_epoch,
    )


def _shape(key: str | None = None) -> IntakeShape:
    key = key or f"h100-storage:{uuid4()}"
    return IntakeShape(
        idempotency_key=key,
        request_reference=f"request:{key}",
        target={"organism": str(uuid4())},
        command={"phase": "advance"},
        omitted=(),
        target_organism_id=None,
    )


def _params(actor: ActorRef, entry: IntakeEntry, shape: IntakeShape):
    fingerprint = command_intake.intake_fingerprint(
        entry=entry,
        action=ACTION,
        actor_identity_id=actor.identity_id,
        actor_access_epoch=actor.access_epoch,
        request_reference=shape.request_reference,
        idempotency_key=shape.idempotency_key,
        target=shape.target,
        command=shape.command,
        omitted=shape.omitted,
    )
    values = [
        actor.pk,
        actor.identity_id,
        actor.access_epoch,
        entry.value,
        ACTION,
        shape.idempotency_key,
        shape.request_reference,
        fingerprint,
        command_intake.FINGERPRINT_SCHEMA,
        shape.target_organism_id,
    ]
    return values, fingerprint


class H100IntakeStorageTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def setUp(self):
        self.actor = _actor_ref(f"h100-storage-{uuid4().hex}")

    def _insert(self, *, entry=IntakeEntry.ADVANCE_PHASE, key=None, target=None):
        shape = _shape(key)
        values, fingerprint = _params(self.actor, entry, shape)
        values[-1] = target
        with connection.cursor() as cursor:
            cursor.execute(command_intake.INSERT_COMMAND_INTAKE_SQL, values)
            row = cursor.fetchone()
        return row, shape, fingerprint

    def _assert_integrity_violation(self, sql, params=(), *, sqlstate="23000"):
        with self.assertRaises(DatabaseError) as raised:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql, params)
        cause = raised.exception.__cause__
        self.assertEqual(getattr(cause, "sqlstate", None), sqlstate)

    def test_iw_17_catalogue_has_required_constraint_trigger_and_index(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname, condeferrable, pg_get_constraintdef(oid)
                  FROM pg_constraint
                 WHERE conrelid = 'public.core_commandintake'::regclass
                 ORDER BY conname
                """
            )
            constraints = {name: (deferrable, definition) for name, deferrable, definition in cursor.fetchall()}
            cursor.execute(
                """
                SELECT tgname, pg_get_triggerdef(oid)
                  FROM pg_trigger
                 WHERE tgrelid = 'public.core_commandintake'::regclass
                   AND NOT tgisinternal
                 ORDER BY tgname
                """
            )
            triggers = dict(cursor.fetchall())
            cursor.execute(
                """
                SELECT indexdef FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND tablename = 'core_authoritydecision'
                   AND indexname = 'h100_intake_decision_history_idx'
                """
            )
            decision_index = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM pg_catalog.pg_trigger t "
                "JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND NOT t.tgisinternal"
            )
            trigger_count = cursor.fetchone()[0]
            cursor.execute("SHOW transaction_isolation")
            isolation = cursor.fetchone()[0]

        unique_deferrable, unique_definition = constraints[
            "h100_intake_actor_action_key_uniq"
        ]
        self.assertFalse(unique_deferrable)
        self.assertIn("UNIQUE (actor_id, action, idempotency_key)", unique_definition)
        self.assertIn("h100_intake_entry_valid", constraints)
        self.assertIn("h100_intake_compound_target_match", constraints)
        self.assertIn("h100_intake_fingerprint_valid", constraints)
        self.assertIn("h100_intake_epoch_nonnegative", constraints)
        self.assertEqual(
            set(triggers),
            {
                "s015_h100_command_intake_assign_time",
                "s015_h100_command_intake_update_immutable",
                "s015_h100_command_intake_delete_immutable",
                "s015_h100_command_intake_truncate_immutable",
            },
        )
        update_definition = triggers["s015_h100_command_intake_update_immutable"]
        for column in (
            "id", "actor_id", "actor_identity_id", "actor_access_epoch", "entry",
            "action", "idempotency_key", "request_reference", "request_fingerprint",
            "fingerprint_schema", "target_organism_id", "received_at",
        ):
            self.assertIn(column, update_definition)
        self.assertIsNotNone(decision_index)
        self.assertIn("(actor_id, action, idempotency_key)", decision_index[0])
        head = current_applied_core_head(connection)[0][1]
        self.assertEqual(trigger_count, REQUIRED_TRIGGER_COUNT[head])
        self.assertEqual(isolation.lower(), "read committed")

    def test_iw_17_both_directions_of_compound_target_check(self):
        ordinary, _, _ = self._insert(entry=IntakeEntry.ADVANCE_PHASE, target=None)
        self.assertIsNotNone(ordinary)
        self._assert_integrity_violation(
            """
            INSERT INTO public.core_commandintake (
                actor_id, actor_identity_id, actor_access_epoch, entry, action,
                idempotency_key, request_reference, request_fingerprint,
                fingerprint_schema, target_organism_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            _params(
                self.actor,
                IntakeEntry.ADVANCE_PHASE,
                _shape(),
            )[0][:-1] + [uuid4()],
            sqlstate="23514",
        )
        self._assert_integrity_violation(
            """
            INSERT INTO public.core_commandintake (
                actor_id, actor_identity_id, actor_access_epoch, entry, action,
                idempotency_key, request_reference, request_fingerprint,
                fingerprint_schema, target_organism_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            _params(
                self.actor,
                IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
                _shape(),
            )[0],
            sqlstate="23514",
        )
        valid_compound, _, _ = self._insert(
            entry=IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
            target=uuid4(),
        )
        self.assertIsNotNone(valid_compound)

    def test_iw_17_time_trigger_mutation_guards_and_reverse_guard(self):
        row, _, _ = self._insert()
        self.assertIsNotNone(row[1])
        self._assert_integrity_violation(
            """
            INSERT INTO public.core_commandintake (
                actor_id, actor_identity_id, actor_access_epoch, entry, action,
                idempotency_key, request_reference, request_fingerprint,
                fingerprint_schema, target_organism_id, received_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp())
            """,
            _params(self.actor, IntakeEntry.ADVANCE_PHASE, _shape())[0],
        )

        columns = (
            "id", "actor_id", "actor_identity_id", "actor_access_epoch", "entry",
            "action", "idempotency_key", "request_reference", "request_fingerprint",
            "fingerprint_schema", "target_organism_id", "received_at",
        )
        for column in columns:
            self._assert_integrity_violation(
                f"UPDATE public.core_commandintake SET {column} = {column} WHERE id = %s",
                [row[0]],
            )
        self._assert_integrity_violation(
            "DELETE FROM public.core_commandintake WHERE id = %s",
            [row[0]],
        )
        self._assert_integrity_violation("TRUNCATE TABLE public.core_commandintake")

        stored = CommandIntake.objects.get(pk=row[0])
        with self.assertRaises(ValidationError):
            stored.save()
        with self.assertRaises(ValidationError):
            stored.delete()

        schema_editor = SimpleNamespace(connection=connection)
        uninstall = import_module(
            "core.migrations.0049_h100_command_intake"
        ).uninstall
        with self.assertRaisesRegex(RuntimeError, "0049_h100_command_intake"):
            uninstall(None, schema_editor)
        self.assertEqual(CommandIntake.objects.count(), 1)

    def test_iw_18_conflict_returning_and_fresh_read_find_the_occupant(self):
        shape = _shape()
        actor = self.actor
        values, _ = _params(actor, IntakeEntry.ADVANCE_PHASE, shape)
        expected = admit_request("default", IntakeEntry.ADVANCE_PHASE, actor, shape)
        with connection.cursor() as cursor:
            cursor.execute(command_intake.INSERT_COMMAND_INTAKE_SQL, values)
            self.assertIsNone(cursor.fetchone())
            cursor.execute(
                """
                SELECT id, received_at FROM public.core_commandintake
                 WHERE actor_id = %s AND action = %s AND idempotency_key = %s
                """,
                [actor.pk, ACTION, shape.idempotency_key],
            )
            row = cursor.fetchone()
        self.assertEqual(row, (expected.intake_id, expected.received_at))

    def test_iw_18_repeatable_read_negative_control_does_not_see_later_commit(self):
        shape = _shape()
        ready = Event()
        committed = Event()
        observed = Queue()

        def stale_reader():
            with transaction.atomic(using="default"):
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    cursor.execute(
                        "SELECT id FROM public.core_commandintake WHERE idempotency_key = %s",
                        [shape.idempotency_key],
                    )
                    before = cursor.fetchone()
                ready.set()
                if not committed.wait(timeout=10.0):
                    raise AssertionError("writer did not commit before the snapshot check")
                with connections["default"].cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM public.core_commandintake WHERE idempotency_key = %s",
                        [shape.idempotency_key],
                    )
                    after = cursor.fetchone()
            observed.put((before, after))

        worker = start_worker(stale_reader)
        try:
            self.assertTrue(ready.wait(timeout=10.0))
            admitted = admit_request(
                "default",
                IntakeEntry.ADVANCE_PHASE,
                self.actor,
                shape,
            )
            committed.set()
            self.assertEqual(worker.result(timeout=12.0), "ok")
        finally:
            committed.set()
            cleanup_workers([worker], timeout=12.0)
        self.assertEqual(observed.get(timeout=2.0), (None, None))
        self.assertTrue(CommandIntake.objects.filter(pk=admitted.intake_id).exists())

    def test_iw_30_receipt_time_is_database_assigned_not_host_time(self):
        shape = _shape()
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            t0 = cursor.fetchone()[0]

        class ShiftedHostDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(hours=1)

        with patch.object(command_intake, "datetime", ShiftedHostDateTime):
            bound = admit_request(
                "default",
                IntakeEntry.ADVANCE_PHASE,
                self.actor,
                shape,
            )

        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            t1 = cursor.fetchone()[0]
        shifted_host_now = ShiftedHostDateTime.now(timezone.utc)
        self.assertLessEqual(t0, bound.received_at)
        self.assertLessEqual(bound.received_at, t1)
        self.assertNotEqual(bound.received_at, shifted_host_now)
        self.assertEqual(
            CommandIntake.objects.get(pk=bound.intake_id).received_at,
            bound.received_at,
        )
