"""Witness H100 admission, connection-state refusal, history checks, and index races."""

from __future__ import annotations

from datetime import datetime, timezone
from queue import Queue
from unittest.mock import patch
from uuid import uuid4

from django.contrib.sessions.models import Session
from django.db import OperationalError, connection, connections, transaction
from django.db.backends.utils import CursorWrapper
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import CommandIntake, Identity, IntakeEntry
from core.organism_contract import OrganismAction
from src.intevia.services import command_intake
from src.intevia.services.command_intake import (
    ActorRef,
    IntakeBinding,
    IntakeAdmissionTechnicalFailure,
    IntakeRecord,
    IntakeShape,
    IntakeUnexpectedHistory,
    S015CommandAtomicityRequired,
    S015CommandContended,
    _require_not_in_atomic_block,
    _resolve_actor,
    admit_request,
    intake_fingerprint,
)
from src.intevia.services.organism_command_support import S015CommandReplayMismatch
from tests.s015_concurrency_support import (
    backend_pid,
    cleanup_workers,
    start_worker,
    wait_until_blocked,
)
from tests.s015_0021_support import create_test_identity


ACTION = OrganismAction.TRANSITION_LIVING_ORGANISM.value


def _shape(key: str | None = None, command_value: str = "advance") -> IntakeShape:
    key = key or f"h100:{uuid4()}"
    return IntakeShape(
        idempotency_key=key,
        request_reference=f"request:{key}",
        target={"organism": str(uuid4())},
        command={"phase": command_value},
        omitted=(),
        target_organism_id=None,
    )


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


def _insert_params(actor: ActorRef, entry: IntakeEntry, shape: IntakeShape):
    fingerprint = intake_fingerprint(
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
    return [
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


class H100IntakeAdmissionTests(TransactionTestCase):
    ISOLATION_FULL_CAPTURE = True

    def setUp(self):
        self.actor = _actor_ref(f"h100-admission-{uuid4().hex}")
        self.entry = IntakeEntry.ADVANCE_PHASE

    def _admit(self, shape):
        return admit_request("default", self.entry, self.actor, shape)

    def _race(self, contender_shape, holder_action):
        holder_pid = backend_pid(connection)
        params = _insert_params(self.actor, self.entry, contender_shape)
        connection.set_autocommit(False)
        with connection.cursor() as cursor:
            cursor.execute(command_intake.INSERT_COMMAND_INTAKE_SQL, params)
            held_row = cursor.fetchone()
        self.assertIsNotNone(held_row)

        results = Queue()

        def contend():
            with connections["default"].cursor() as cursor:
                cursor.execute("SET lock_timeout = '10s'")
            result = admit_request("default", self.entry, self.actor, contender_shape)
            results.put(result)

        worker = start_worker(contend)
        try:
            contender_pid = worker.pid()
            with connection.cursor() as cursor:
                wait_until_blocked(
                    cursor,
                    contender_pid,
                    holder_pid,
                    query_fragment="INSERT INTO public.core_commandintake",
                    timeout=5.0,
                )
            holder_action()
            self.assertEqual(worker.result(timeout=12.0), "ok")
            return held_row, results.get(timeout=2.0)
        finally:
            if not connection.get_autocommit():
                connection.rollback()
                connection.set_autocommit(True)
            cleanup_workers([worker], timeout=12.0)

    def test_admission_lock_timeout_is_contended_and_saves_nothing(self):
        shape = _shape()
        holder_pid = backend_pid(connection)
        params = _insert_params(self.actor, self.entry, shape)
        connection.set_autocommit(False)
        with connection.cursor() as cursor:
            cursor.execute(command_intake.INSERT_COMMAND_INTAKE_SQL, params)
            held_row = cursor.fetchone()
        self.assertIsNotNone(held_row)

        def contend():
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '200ms'")
                self._admit(shape)
            finally:
                with connections["default"].cursor() as cursor:
                    cursor.execute("RESET lock_timeout")

        worker = None
        try:
            worker = start_worker(contend)
            contender_pid = worker.pid()
            with connection.cursor() as cursor:
                wait_until_blocked(
                    cursor,
                    contender_pid,
                    holder_pid,
                    query_fragment="INSERT INTO public.core_commandintake",
                    timeout=5.0,
                )
            result = worker.result(timeout=5.0)
            self.assertIsInstance(result, S015CommandContended)
            self.assertEqual(result.diagnostic_sqlstate, "55P03")
            self.assertEqual(
                str(result),
                "Your request could not be completed because it conflicted with another database operation. "
                "This attempt made no changes. Please wait a moment before trying again. "
                "If this keeps happening, note the time it occurred; it needs to be checked.",
            )
            self.assertIsInstance(result.__cause__, OperationalError)
            self.assertEqual(
                getattr(result.__cause__.__cause__, "sqlstate", None),
                "55P03",
            )
        finally:
            if worker is not None:
                cleanup_workers([worker], timeout=12.0)
            if not connection.get_autocommit():
                connection.rollback()
                connection.set_autocommit(True)

        self.assertFalse(
            CommandIntake.objects.filter(
                actor_id=self.actor.pk,
                action=ACTION,
                idempotency_key=shape.idempotency_key,
            ).exists()
        )
        self.assertTrue(self._admit(shape).first)

    def test_admission_statement_timeout_is_technical_and_saves_nothing(self):
        shape = _shape()
        holder_pid = backend_pid(connection)
        params = _insert_params(self.actor, self.entry, shape)
        connection.set_autocommit(False)
        with connection.cursor() as cursor:
            cursor.execute(command_intake.INSERT_COMMAND_INTAKE_SQL, params)
            held_row = cursor.fetchone()
        self.assertIsNotNone(held_row)

        def contend():
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET lock_timeout = 0")
                    cursor.execute("SET statement_timeout = '200ms'")
                self._admit(shape)
            finally:
                with connections["default"].cursor() as cursor:
                    cursor.execute("RESET lock_timeout")
                    cursor.execute("RESET statement_timeout")

        worker = None
        try:
            worker = start_worker(contend)
            contender_pid = worker.pid()
            with connection.cursor() as cursor:
                wait_until_blocked(
                    cursor,
                    contender_pid,
                    holder_pid,
                    query_fragment="INSERT INTO public.core_commandintake",
                    timeout=5.0,
                )
            result = worker.result(timeout=5.0)
            self.assertIsInstance(result, IntakeAdmissionTechnicalFailure)
            self.assertEqual(
                str(result),
                f"{ACTION} could not be admitted because of a technical problem; "
                "nothing was saved. Try again; if it persists, report it.",
            )
            self.assertEqual(
                getattr(result.__cause__.__cause__, "sqlstate", None),
                "57014",
            )
        finally:
            if worker is not None:
                cleanup_workers([worker], timeout=12.0)
            if not connection.get_autocommit():
                connection.rollback()
                connection.set_autocommit(True)

        self.assertFalse(
            CommandIntake.objects.filter(
                actor_id=self.actor.pk,
                action=ACTION,
                idempotency_key=shape.idempotency_key,
            ).exists()
        )

    def test_admission_connection_loss_is_uncertain_injected(self):
        shape = _shape()
        original_execute = CursorWrapper.execute
        injected_error = OperationalError("injected admission connection loss")
        no_sqlstate_cause = RuntimeError("injected connection failure")

        def execute_with_connection_loss(cursor, sql, params=None):
            if sql == command_intake.INSERT_COMMAND_INTAKE_SQL:
                raise injected_error from no_sqlstate_cause
            return original_execute(cursor, sql, params)

        with patch.object(CursorWrapper, "execute", new=execute_with_connection_loss):
            with self.assertRaises(IntakeAdmissionTechnicalFailure) as raised:
                self._admit(shape)

        self.assertEqual(
            str(raised.exception),
            f"{ACTION} could not be confirmed because the connection to the database was lost; "
            "this request may or may not have been received. Send the same request again with the same "
            "request key: INTEVIA will find the earlier receipt or receive it now. If it persists, report it.",
        )
        self.assertIs(raised.exception.__cause__, injected_error)
        self.assertIsNone(getattr(injected_error.__cause__, "sqlstate", None))

    def test_first_admission_and_identical_retry_share_the_database_time(self):
        shape = _shape()
        first = self._admit(shape)
        replay = self._admit(shape)

        stored = CommandIntake.objects.get(pk=first.intake_id)
        self.assertTrue(first.first)
        self.assertFalse(replay.first)
        self.assertEqual(first.received_at, stored.received_at)
        self.assertEqual(replay.received_at, stored.received_at)
        self.assertEqual(
            CommandIntake.objects.filter(
                actor_id=self.actor.pk,
                action=ACTION,
                idempotency_key=shape.idempotency_key,
            ).count(),
            1,
        )

    def test_iw_09a_committed_holder_is_read_after_conflict(self):
        shape = _shape()
        held, loser = self._race(shape, connection.commit)
        self.assertFalse(loser.first)
        self.assertEqual(loser.intake_id, held[0])
        self.assertEqual(loser.received_at, held[1])
        self.assertEqual(
            CommandIntake.objects.filter(idempotency_key=shape.idempotency_key).count(),
            1,
        )

    def test_iw_10_rolled_back_holder_leaves_the_contender_as_the_only_row(self):
        shape = _shape()
        _, contender = self._race(shape, connection.rollback)
        row = CommandIntake.objects.get(idempotency_key=shape.idempotency_key)
        self.assertTrue(contender.first)
        self.assertEqual(contender.intake_id, row.pk)
        self.assertEqual(contender.received_at, row.received_at)
        self.assertEqual(row.request_reference, shape.request_reference)
        self.assertEqual(row.request_fingerprint, _insert_params(self.actor, self.entry, shape)[7])
        self.assertEqual(
            CommandIntake.objects.filter(idempotency_key=shape.idempotency_key).count(),
            1,
        )

    def test_iw_11_changed_content_is_refused_after_the_holder_commits(self):
        holder_shape = _shape()
        contender_shape = IntakeShape(
            idempotency_key=holder_shape.idempotency_key,
            request_reference=holder_shape.request_reference,
            target=holder_shape.target,
            command={"phase": "different"},
            omitted=(),
            target_organism_id=None,
        )
        holder_pid = backend_pid(connection)
        params = _insert_params(self.actor, self.entry, holder_shape)
        connection.set_autocommit(False)
        with connection.cursor() as cursor:
            cursor.execute(command_intake.INSERT_COMMAND_INTAKE_SQL, params)
            held_row = cursor.fetchone()
        self.assertIsNotNone(held_row)

        def contend():
            with connections["default"].cursor() as cursor:
                cursor.execute("SET lock_timeout = '10s'")
            return admit_request("default", self.entry, self.actor, contender_shape)

        worker = start_worker(contend)
        try:
            with connection.cursor() as cursor:
                wait_until_blocked(
                    cursor,
                    worker.pid(),
                    holder_pid,
                    query_fragment="INSERT INTO public.core_commandintake",
                    timeout=5.0,
                )
            connection.commit()
            result = worker.result(timeout=12.0)
            self.assertIsInstance(result, S015CommandReplayMismatch)
        finally:
            if not connection.get_autocommit():
                connection.rollback()
                connection.set_autocommit(True)
            cleanup_workers([worker], timeout=12.0)

        row = CommandIntake.objects.get(id=held_row[0])
        self.assertEqual(row.request_fingerprint, _insert_params(self.actor, self.entry, holder_shape)[7])
        self.assertEqual(
            CommandIntake.objects.filter(idempotency_key=holder_shape.idempotency_key).count(),
            1,
        )

    def test_iw_19_atomic_and_rollback_states_refuse_without_boundary_sql(self):
        shape = _shape()
        with transaction.atomic():
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises(S015CommandAtomicityRequired):
                    self._admit(shape)
            self.assertEqual(queries.captured_queries, [])

        prior_needs_rollback = connection.needs_rollback
        connection.needs_rollback = True
        try:
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises(S015CommandAtomicityRequired):
                    self._admit(shape)
            self.assertEqual(queries.captured_queries, [])
        finally:
            connection.needs_rollback = prior_needs_rollback
        self.assertFalse(CommandIntake.objects.filter(idempotency_key=shape.idempotency_key).exists())

    def test_actor_resolution_is_one_identity_lookup_and_atomicity_guard_is_technical(self):
        identity = Identity.objects.get(pk=self.actor.pk)
        resolved = _resolve_actor("default", identity.credential)
        self.assertEqual(
            (resolved.pk, resolved.identity_id, resolved.access_epoch),
            (self.actor.pk, self.actor.identity_id, self.actor.access_epoch),
        )
        _require_not_in_atomic_block("default")
        with transaction.atomic():
            with self.assertRaises(S015CommandAtomicityRequired):
                _require_not_in_atomic_block("default")

    def test_iw_19_manual_transaction_preserves_callers_write(self):
        shape = _shape()
        session_key = uuid4().hex
        connection.set_autocommit(False)
        try:
            Session.objects.create(
                session_key=session_key,
                session_data="fixture",
                expire_date=datetime(2030, 1, 1, tzinfo=timezone.utc),
            )
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises(S015CommandAtomicityRequired):
                    self._admit(shape)
            self.assertEqual(queries.captured_queries, [])

            during = Queue()
            reader = start_worker(
                lambda: during.put(
                    Session.objects.filter(session_key=session_key).exists()
                )
            )
            self.assertEqual(reader.result(timeout=10.0), "ok")
            self.assertFalse(during.get(timeout=2.0))

            connection.commit()
            after = Queue()
            reader = start_worker(
                lambda: after.put(
                    Session.objects.filter(session_key=session_key).exists()
                )
            )
            self.assertEqual(reader.result(timeout=10.0), "ok")
            self.assertTrue(after.get(timeout=2.0))
        finally:
            if not connection.get_autocommit():
                connection.rollback()
                connection.set_autocommit(True)
        self.assertFalse(CommandIntake.objects.filter(idempotency_key=shape.idempotency_key).exists())

    def test_history_hit_accounted_by_another_binding_is_admitted(self):
        shape = _shape()
        received_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        record = IntakeRecord(
            id=1,
            actor_id=self.actor.pk,
            action=ACTION,
            idempotency_key=shape.idempotency_key,
            kind="decision",
            evaluated_at=received_at,
        )
        binding = IntakeBinding(
            actor_id=self.actor.pk,
            entry=self.entry,
            action=ACTION,
            idempotency_key=shape.idempotency_key,
            target_organism_id=None,
            received_at=received_at,
        )
        with (
            patch.object(command_intake, "_history_records", return_value=[record]),
            patch.object(command_intake, "_candidate_bindings", return_value=[binding]),
        ):
            result = self._admit(shape)
        self.assertTrue(result.first)
        self.assertEqual(
            CommandIntake.objects.filter(idempotency_key=shape.idempotency_key).count(),
            1,
        )

    def test_unaccounted_history_refuses_without_minting(self):
        shape = _shape()
        record = IntakeRecord(
            id=1,
            actor_id=self.actor.pk,
            action=ACTION,
            idempotency_key=shape.idempotency_key,
            kind="receipt",
            evaluated_at=None,
        )
        with (
            patch.object(command_intake, "_history_records", return_value=[record]),
            patch.object(command_intake, "_candidate_bindings", return_value=[]),
        ):
            with self.assertRaises(IntakeUnexpectedHistory):
                self._admit(shape)
        self.assertFalse(CommandIntake.objects.filter(idempotency_key=shape.idempotency_key).exists())

    def test_recheck_row_present_branch_is_labelled_injected(self):
        shape = _shape()
        fingerprint = _insert_params(self.actor, self.entry, shape)[7]
        received_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        existing_row = (
            99,
            self.entry.value,
            shape.request_reference,
            fingerprint,
            command_intake.FINGERPRINT_SCHEMA,
            received_at,
            None,
        )
        record = IntakeRecord(
            id=1,
            actor_id=self.actor.pk,
            action=ACTION,
            idempotency_key=shape.idempotency_key,
            kind="receipt",
            evaluated_at=None,
        )
        with (
            patch.object(command_intake, "_existing_row", side_effect=[None, existing_row]) as lookup,
            patch.object(command_intake, "_history_records", return_value=[record]),
            patch.object(command_intake, "_candidate_bindings", return_value=[]),
        ):
            result = self._admit(shape)
        self.assertFalse(result.first)
        self.assertEqual(result.received_at, received_at)
        self.assertEqual(lookup.call_count, 2)
        self.assertFalse(CommandIntake.objects.filter(idempotency_key=shape.idempotency_key).exists())


class H100IntakeAdmissionTestCaseGuardTests(TestCase):
    ISOLATION_FULL_CAPTURE = True

    def test_iw_19_testcase_connection_is_refused_without_sql(self):
        actor = ActorRef(pk=1, identity_id=uuid4(), access_epoch=0)
        shape = _shape()
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(S015CommandAtomicityRequired):
                admit_request("default", IntakeEntry.ADVANCE_PHASE, actor, shape)
        self.assertEqual(queries.captured_queries, [])
