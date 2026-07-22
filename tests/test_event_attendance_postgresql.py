from datetime import datetime, timedelta, timezone
from threading import Barrier, Event as ThreadEvent, Lock, Thread
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase

from core.models import (
    Event,
    EventAttendance,
    EventAttendanceEvidenceReference,
    EventAttendanceTransition,
    EventRegistration,
    Identity,
    ProfileRole,
    Role,
)
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.event_attendance_service import (
    AttendanceEvidence,
    AttendanceIdempotencyConflict,
    AttendanceNotAuthorised,
    DuplicateEventAttendance,
    EventAttendanceAuthority,
    EventAttendanceService,
    InvalidAttendance,
    WalkInEligibility,
)
from src.intevia.services.event_registration_service import (
    EventRegistrationService,
)
from src.intevia.services.event_service import EventService


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL S008 qualification guardian",
)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


class PausingCapability(Capability):
    def __init__(self, action, entered, release):
        self.action = action
        self.entered = entered
        self.release = release
        self._paused = False
        self._lock = Lock()

    def authorise(self, *, identity, action, target, timestamp):
        with self._lock:
            pause = action == self.action and not self._paused
            if pause:
                self._paused = True
        if pause:
            self.entered.set()
            self.release.wait(timeout=5)
        return super().authorise(
            identity=identity,
            action=action,
            target=target,
            timestamp=timestamp,
        )


@POSTGRESQL_ONLY
class EventAttendancePostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.actor_user = User.objects.create_user(username="pg-attendance-actor")
        self.actor = Identity.objects.create(
            credential=self.actor_user,
            access_state=Identity.AccessState.ACTIVE,
        )
        self.other_actor_user = User.objects.create_user(
            username="pg-attendance-other-actor"
        )
        self.other_actor = Identity.objects.create(
            credential=self.other_actor_user,
            access_state=Identity.AccessState.ACTIVE,
        )
        self.subject_user = User.objects.create_user(username="pg-attendance-subject")
        self.subject = Identity.objects.create(
            credential=self.subject_user,
            access_state=Identity.AccessState.ACTIVE,
        )
        role = Role.objects.create(
            pk=1_000_008,
            name="PostgreSQL attendance cross-slice actor",
        )
        ProfileRole.objects.create(identity=self.actor, role=role)
        self.event = Event.objects.create(
            event_id="event:pg:attendance",
            title="PostgreSQL attendance",
            owner=self.actor,
            state=Event.State.ACTIVE,
            created_at=NOW,
        )
        self.registration = EventRegistration.objects.create(
            registration_id="registration:pg:attendance",
            event=self.event,
            participant=self.subject,
            state=EventRegistration.State.REGISTERED,
            origin=EventRegistration.Origin.THIRD_PARTY,
            event_state_at_registration=Event.State.ACTIVE,
            eligibility_basis_type=(
                EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION
            ),
            eligibility_basis_reference="eligibility:pg:attendance",
            eligibility_evaluated_at=NOW,
            registered_at=NOW,
        )

    @staticmethod
    def evidence(reference="evidence:pg:attendance"):
        return (
            AttendanceEvidence(
                reference=reference,
                evidence_type="actor_assertion",
                provenance="authorised_actor",
            ),
        )

    @staticmethod
    def service(capability=None):
        return EventAttendanceService(
            authority=EventAttendanceAuthority(capability or Capability()),
            clock=lambda: NOW,
        )

    def record(self, *, service=None, user=None, suffix="one", key=None, evidence=None):
        return (service or self.service()).record_attendance(
            identity=user or self.actor_user,
            attendance_id=f"attendance:pg:{suffix}",
            event_id=self.event.event_id,
            subject=self.subject,
            origin=EventAttendance.Origin.REGISTERED,
            supporting_registration=self.registration,
            evidence=evidence or self.evidence(f"evidence:pg:{suffix}"),
            idempotency_key=key or f"attendance:pg:{suffix}",
        )

    @staticmethod
    def run_concurrently(functions):
        barrier = Barrier(len(functions))
        result_lock = Lock()
        results = []

        def invoke(function):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                result = function()
            except Exception as error:
                result = error
            finally:
                close_old_connections()
            with result_lock:
                results.append(result)

        threads = [Thread(target=invoke, args=(function,)) for function in functions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        if any(thread.is_alive() for thread in threads):
            raise AssertionError("concurrent attendance guardian did not complete")
        return results

    def thread_record(self, *, suffix, key, user_pk=None, evidence_reference=None):
        def command():
            user = User.objects.get(pk=user_pk or self.actor_user.pk)
            subject = Identity.objects.get(pk=self.subject.pk)
            registration = EventRegistration.objects.get(pk=self.registration.pk)
            return self.service().record_attendance(
                identity=user,
                attendance_id=f"attendance:pg:{suffix}",
                event_id=self.event.event_id,
                subject=subject,
                origin=EventAttendance.Origin.REGISTERED,
                supporting_registration=registration,
                evidence=self.evidence(evidence_reference or f"evidence:pg:{suffix}"),
                idempotency_key=key,
            )

        return command

    def test_concurrent_duplicate_first_record_has_one_governed_winner(self):
        results = self.run_concurrently(
            [
                self.thread_record(suffix="duplicate-one", key="key:one"),
                self.thread_record(suffix="duplicate-two", key="key:two"),
            ]
        )
        self.assertEqual(sum(isinstance(x, EventAttendance) for x in results), 1)
        self.assertEqual(
            sum(isinstance(x, DuplicateEventAttendance) for x in results),
            1,
        )
        self.assertEqual(EventAttendance.objects.count(), 1)

    def test_concurrent_same_key_replays_one_complete_result(self):
        command = self.thread_record(suffix="same-key", key="key:same")
        results = self.run_concurrently([command, command])
        self.assertTrue(all(isinstance(x, EventAttendance) for x in results))
        self.assertEqual(results[0].pk, results[1].pk)
        attendance = EventAttendance.objects.get()
        self.assertEqual(attendance.transitions.count(), 1)
        self.assertEqual(attendance.transitions.get().evidence_references.count(), 1)

    def test_concurrent_same_key_changed_payload_conflicts(self):
        results = self.run_concurrently(
            [
                self.thread_record(
                    suffix="changed",
                    key="key:changed",
                    evidence_reference="evidence:changed:one",
                ),
                self.thread_record(
                    suffix="changed",
                    key="key:changed",
                    evidence_reference="evidence:changed:two",
                ),
            ]
        )
        self.assertEqual(sum(isinstance(x, EventAttendance) for x in results), 1)
        self.assertEqual(
            sum(isinstance(x, AttendanceIdempotencyConflict) for x in results),
            1,
        )

    def test_two_actor_duplicate_creation_is_not_replay(self):
        results = self.run_concurrently(
            [
                self.thread_record(suffix="actor-one", key="key:shared"),
                self.thread_record(
                    suffix="actor-two",
                    key="key:shared",
                    user_pk=self.other_actor_user.pk,
                ),
            ]
        )
        self.assertEqual(sum(isinstance(x, EventAttendance) for x in results), 1)
        self.assertEqual(
            sum(isinstance(x, DuplicateEventAttendance) for x in results),
            1,
        )

    def test_record_waiting_on_event_completion_observes_terminal_state(self):
        locked = ThreadEvent()
        release = ThreadEvent()

        def complete():
            close_old_connections()
            try:
                service = EventService(
                    authority=ContributionAuthority(
                        PausingCapability("complete_event", locked, release)
                    )
                )
                service.transition_event(
                    identity=User.objects.get(pk=self.actor_user.pk),
                    event_id=self.event.event_id,
                    command="complete_event",
                    occurred_at=NOW,
                )
            finally:
                close_old_connections()

        thread = Thread(target=complete)
        thread.start()
        self.assertTrue(locked.wait(timeout=5))
        record_result = []

        def record():
            close_old_connections()
            try:
                record_result.append(self.thread_record(suffix="completion", key="key:completion")())
            except Exception as error:
                record_result.append(error)
            finally:
                close_old_connections()

        record_thread = Thread(target=record)
        record_thread.start()
        release.set()
        thread.join(timeout=10)
        record_thread.join(timeout=10)
        self.assertFalse(thread.is_alive() or record_thread.is_alive())
        self.assertIsInstance(record_result[0], InvalidAttendance)
        self.assertFalse(EventAttendance.objects.exists())

    def test_record_waiting_on_registration_cancellation_is_denied(self):
        locked = ThreadEvent()
        release = ThreadEvent()

        def cancel():
            close_old_connections()
            try:
                service = EventRegistrationService(
                    authority=ContributionAuthority(
                        PausingCapability("cancel", locked, release)
                    )
                )
                service.cancel(
                    identity=User.objects.get(pk=self.actor_user.pk),
                    registration_id=self.registration.registration_id,
                    cancellation_basis="administrative",
                    basis_source="actor_recorded",
                    occurred_at=NOW,
                )
            finally:
                close_old_connections()

        thread = Thread(target=cancel)
        thread.start()
        self.assertTrue(locked.wait(timeout=5))
        results = []

        def record():
            close_old_connections()
            try:
                results.append(self.thread_record(suffix="cancel", key="key:cancel")())
            except Exception as error:
                results.append(error)
            finally:
                close_old_connections()

        record_thread = Thread(target=record)
        record_thread.start()
        release.set()
        thread.join(timeout=10)
        record_thread.join(timeout=10)
        self.assertFalse(thread.is_alive() or record_thread.is_alive())
        self.assertIsInstance(results[0], InvalidAttendance)
        self.assertFalse(EventAttendance.objects.exists())

    def test_actor_deactivation_during_capability_fails_final_recheck(self):
        entered = ThreadEvent()
        release = ThreadEvent()
        service = self.service(
            PausingCapability("record_attendance", entered, release)
        )
        results = []

        def record():
            close_old_connections()
            try:
                results.append(
                    service.record_attendance(
                        identity=User.objects.get(pk=self.actor_user.pk),
                        attendance_id="attendance:pg:deactivation",
                        event_id=self.event.event_id,
                        subject=Identity.objects.get(pk=self.subject.pk),
                        origin=EventAttendance.Origin.REGISTERED,
                        supporting_registration=EventRegistration.objects.get(
                            pk=self.registration.pk
                        ),
                        evidence=self.evidence("evidence:deactivation"),
                        idempotency_key="key:deactivation",
                    )
                )
            except Exception as error:
                results.append(error)
            finally:
                close_old_connections()

        thread = Thread(target=record)
        thread.start()
        self.assertTrue(entered.wait(timeout=5))
        Identity.objects.filter(pk=self.actor.pk).update(
            access_state=Identity.AccessState.DEACTIVATED,
            access_epoch=1,
        )
        release.set()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(results[0], AttendanceNotAuthorised)
        self.assertFalse(EventAttendance.objects.exists())

    def test_competing_corrections_serialize_into_linear_sequence(self):
        attendance = self.record(suffix="corrections")
        entered = ThreadEvent()
        release = ThreadEvent()
        capability = PausingCapability("correct_attendance", entered, release)

        def correct(offset, key):
            def command():
                return self.service(capability).correct_attendance(
                    identity=User.objects.get(pk=self.actor_user.pk),
                    attendance_id=attendance.attendance_id,
                    resulting_observed_at=NOW + timedelta(minutes=offset),
                    resulting_supporting_registration=EventRegistration.objects.get(
                        pk=self.registration.pk
                    ),
                    rationale=f"correction {offset}",
                    evidence=self.evidence(f"evidence:correction:{offset}"),
                    idempotency_key=key,
                )

            return command

        first_result = []
        first = Thread(target=lambda: first_result.append(correct(1, "key:correct:one")()))
        first.start()
        self.assertTrue(entered.wait(timeout=5))
        second_result = []
        second = Thread(target=lambda: second_result.append(correct(2, "key:correct:two")()))
        second.start()
        release.set()
        first.join(timeout=10)
        second.join(timeout=10)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(
            list(attendance.transitions.order_by("sequence").values_list("sequence", flat=True)),
            [1, 2, 3],
        )

    def test_correction_then_void_serialize_without_competing_successors(self):
        attendance = self.record(suffix="correct-void")
        entered = ThreadEvent()
        release = ThreadEvent()
        capability = PausingCapability("correct_attendance", entered, release)
        results = []

        def correct():
            close_old_connections()
            try:
                results.append(
                    self.service(capability).correct_attendance(
                        identity=User.objects.get(pk=self.actor_user.pk),
                        attendance_id=attendance.attendance_id,
                        resulting_observed_at=NOW + timedelta(minutes=1),
                        resulting_supporting_registration=EventRegistration.objects.get(
                            pk=self.registration.pk
                        ),
                        rationale="correct before void",
                        evidence=self.evidence("evidence:correct-before-void"),
                        idempotency_key="key:correct-before-void",
                    )
                )
            except Exception as error:
                results.append(error)
            finally:
                close_old_connections()

        def void():
            close_old_connections()
            try:
                results.append(
                    self.service().void_attendance(
                        identity=User.objects.get(pk=self.actor_user.pk),
                        attendance_id=attendance.attendance_id,
                        rationale="void after correction",
                        evidence=self.evidence("evidence:void-after-correct"),
                        idempotency_key="key:void-after-correct",
                    )
                )
            except Exception as error:
                results.append(error)
            finally:
                close_old_connections()

        first = Thread(target=correct)
        first.start()
        self.assertTrue(entered.wait(timeout=5))
        second = Thread(target=void)
        second.start()
        release.set()
        first.join(timeout=10)
        second.join(timeout=10)
        self.assertTrue(all(isinstance(x, EventAttendanceTransition) for x in results))
        self.assertEqual(attendance.transitions.count(), 3)
        attendance.refresh_from_db()
        self.assertEqual(attendance.status, EventAttendance.Status.VOIDED)

    def test_void_then_reinstate_serialize_into_linear_sequence(self):
        attendance = self.record(suffix="void-reinstate")
        entered = ThreadEvent()
        release = ThreadEvent()
        capability = PausingCapability("void_attendance", entered, release)
        results = []

        def void():
            close_old_connections()
            try:
                results.append(
                    self.service(capability).void_attendance(
                        identity=User.objects.get(pk=self.actor_user.pk),
                        attendance_id=attendance.attendance_id,
                        rationale="void",
                        evidence=self.evidence("evidence:void-race"),
                        idempotency_key="key:void-race",
                    )
                )
            except Exception as error:
                results.append(error)
            finally:
                close_old_connections()

        def reinstate():
            close_old_connections()
            try:
                results.append(
                    self.service().reinstate_attendance(
                        identity=User.objects.get(pk=self.actor_user.pk),
                        attendance_id=attendance.attendance_id,
                        rationale="reinstate",
                        evidence=self.evidence("evidence:reinstate-race"),
                        idempotency_key="key:reinstate-race",
                    )
                )
            except Exception as error:
                results.append(error)
            finally:
                close_old_connections()

        first = Thread(target=void)
        first.start()
        self.assertTrue(entered.wait(timeout=5))
        second = Thread(target=reinstate)
        second.start()
        release.set()
        first.join(timeout=10)
        second.join(timeout=10)
        self.assertTrue(all(isinstance(x, EventAttendanceTransition) for x in results))
        self.assertEqual(attendance.transitions.count(), 3)
        attendance.refresh_from_db()
        self.assertEqual(attendance.status, EventAttendance.Status.PRESENT)

    def test_database_arbitrates_initial_successor_and_sequence_uniqueness(self):
        attendance = self.record(suffix="constraints")
        initial = attendance.transitions.get()
        values = {
            "attendance": attendance,
            "sequence": 1,
            "action": EventAttendanceTransition.Action.RECORD,
            "from_status": "unrecorded",
            "to_status": EventAttendance.Status.PRESENT,
            "actor": self.actor,
            "authority_reference": "authority:constraint",
            "authority_evaluated_at": NOW,
            "origin": attendance.origin,
            "resulting_observed_at": NOW,
            "idempotency_key": "key:constraint:initial",
            "payload_fingerprint": "0" * 64,
            "recorded_at": NOW,
        }
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EventAttendanceTransition.objects.bulk_create(
                    [EventAttendanceTransition(**values)]
                )
        successor = dict(values)
        successor.update(
            sequence=2,
            action=EventAttendanceTransition.Action.CORRECT,
            from_status=EventAttendance.Status.PRESENT,
            previous_transition=initial,
            previous_observed_at=NOW,
            idempotency_key="key:constraint:successor",
            payload_fingerprint="1" * 64,
        )
        EventAttendanceTransition.objects.bulk_create(
            [EventAttendanceTransition(**successor)]
        )
        successor["sequence"] = 3
        successor["idempotency_key"] = "key:constraint:second-successor"
        successor["payload_fingerprint"] = "2" * 64
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EventAttendanceTransition.objects.bulk_create(
                    [EventAttendanceTransition(**successor)]
                )

    def test_expected_constraint_failure_recovers_outer_savepoint(self):
        attendance = self.record(suffix="savepoint")
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    EventAttendance.objects.bulk_create(
                        [
                            EventAttendance(
                                attendance_id="attendance:pg:savepoint:duplicate",
                                event=self.event,
                                subject=self.subject,
                                status=EventAttendance.Status.PRESENT,
                                observed_at=NOW,
                                supporting_registration=self.registration,
                                origin=EventAttendance.Origin.REGISTERED,
                            )
                        ]
                    )
            self.assertEqual(EventAttendance.objects.get().pk, attendance.pk)

    def test_unexpected_integrity_failure_propagates_without_residue(self):
        with patch.object(
            EventAttendanceEvidenceReference.objects,
            "create",
            side_effect=IntegrityError("unexpected attendance evidence failure"),
        ):
            with self.assertRaises(IntegrityError):
                self.record(suffix="unexpected")
        self.assertFalse(EventAttendance.objects.exists())
        self.assertFalse(EventAttendanceTransition.objects.exists())
        self.assertFalse(EventAttendanceEvidenceReference.objects.exists())

    def test_walk_in_aggregate_transition_evidence_and_receipt_are_atomic(self):
        service = self.service()
        with patch(
            "src.intevia.services.event_attendance_service."
            "EventAttendanceEligibilityReceipt.objects.create",
            side_effect=IntegrityError("unexpected receipt failure"),
        ):
            with self.assertRaises(IntegrityError):
                service.record_attendance(
                    identity=self.actor_user,
                    attendance_id="attendance:pg:walk-in-atomic",
                    event_id=self.event.event_id,
                    subject=self.other_actor,
                    origin=EventAttendance.Origin.GOVERNED_WALK_IN,
                    evidence=self.evidence("evidence:walk-in-atomic"),
                    walk_in_eligibility=WalkInEligibility(
                        basis_reference="eligibility:walk-in-atomic"
                    ),
                    idempotency_key="key:walk-in-atomic",
                )
        self.assertFalse(EventAttendance.objects.exists())
        self.assertFalse(EventAttendanceTransition.objects.exists())
        self.assertFalse(EventAttendanceEvidenceReference.objects.exists())

    def test_named_constraints_and_indexes_are_in_postgresql_catalogue(self):
        expected_constraints = {
            "unique_event_subject_attendance",
            "attendance_sequence_positive",
            "attendance_transition_not_self_previous",
            "unique_attendance_sequence",
            "unique_attendance_idempotency",
            "unique_attendance_evidence_reference",
        }
        expected_indexes = {
            "event_attendance_lookup_idx",
            "attendance_transition_time_idx",
            "one_initial_attendance_transition",
            "one_attendance_transition_successor",
        }
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conname = ANY(%s)
                """,
                [list(expected_constraints)],
            )
            constraints = {row[0] for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE indexname = ANY(%s)
                """,
                [list(expected_indexes)],
            )
            indexes = {row[0] for row in cursor.fetchall()}
        self.assertEqual(constraints, expected_constraints)
        self.assertEqual(indexes, expected_indexes)
