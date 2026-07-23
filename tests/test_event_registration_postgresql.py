from datetime import datetime, timezone
from threading import Barrier, Event as ThreadEvent, Lock, Thread
from unittest import skipUnless

from django.contrib.auth.models import User
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from core.models import (
    Event,
    EventRegistration,
    EventRegistrationTransition,
    Identity,
    ProfileRole,
    Role,
)
from src.intevia.services.contribution_authority import ContributionAuthority
from src.intevia.services.event_registration_service import (
    DuplicateActiveEventRegistration,
    EventRegistrationService,
    InvalidEventRegistration,
)


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL closure guardian",
)


class Capability:
    def authorise(self, *, identity, action, target, timestamp):
        return f"authority:{identity.pk}:{action}"


@POSTGRESQL_ONLY
class EventRegistrationPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(username="postgres-registration")
        self.profile = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(
            pk=1_000_000,
            name="PostgreSQL registration actor",
        )
        ProfileRole.objects.create(identity=self.profile, role=role)
        self.event = Event.objects.create(
            event_id="event:postgres-registration",
            title="PostgreSQL registration",
            owner=self.profile,
            state=Event.State.PUBLISHED,
            created_at=NOW,
        )

    def test_concurrent_registration_has_one_winner_and_one_duplicate(self):
        barrier = Barrier(2)
        result_lock = Lock()
        results = []

        def register(registration_id):
            close_old_connections()
            try:
                user = User.objects.get(pk=self.user.pk)
                participant = Identity.objects.get(pk=self.profile.pk)
                service = EventRegistrationService(
                    authority=ContributionAuthority(Capability())
                )
                barrier.wait()
                result = service.register(
                    identity=user,
                    registration_id=registration_id,
                    event_id=self.event.event_id,
                    participant=participant,
                    evidence_reference=f"evidence:{registration_id}",
                    eligibility_basis_type=(
                        EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION
                    ),
                    eligibility_basis_reference="event-state:published",
                    occurred_at=NOW,
                )
            except Exception as error:
                result = error
            finally:
                close_old_connections()
            with result_lock:
                results.append(result)

        threads = [
            Thread(target=register, args=(f"registration:race:{number}",))
            for number in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(
            sum(isinstance(result, EventRegistration) for result in results),
            1,
        )
        self.assertEqual(
            sum(
                isinstance(result, DuplicateActiveEventRegistration)
                for result in results
            ),
            1,
        )
        self.assertEqual(EventRegistration.objects.count(), 1)

    def test_concurrent_same_key_replays_the_winning_registration(self):
        barrier = Barrier(2)
        result_lock = Lock()
        results = []

        def register(registration_id):
            close_old_connections()
            try:
                user = User.objects.get(pk=self.user.pk)
                participant = Identity.objects.get(pk=self.profile.pk)
                service = EventRegistrationService(
                    authority=ContributionAuthority(Capability())
                )
                barrier.wait()
                result = service.register(
                    identity=user,
                    registration_id=registration_id,
                    event_id=self.event.event_id,
                    participant=participant,
                    evidence_reference=f"evidence:{registration_id}",
                    eligibility_basis_type=(
                        EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION
                    ),
                    eligibility_basis_reference="event-state:published",
                    idempotency_key="registration-race:same-key",
                    occurred_at=NOW,
                )
            except Exception as error:
                result = error
            finally:
                close_old_connections()
            with result_lock:
                results.append(result)

        threads = [
            Thread(target=register, args=(f"registration:key-race:{number}",))
            for number in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertTrue(
            all(isinstance(result, EventRegistration) for result in results)
        )
        self.assertEqual(results[0].pk, results[1].pk)
        self.assertEqual(EventRegistration.objects.count(), 1)

    def test_registration_waiting_on_completion_observes_completed_event(self):
        Event.objects.filter(pk=self.event.pk).update(state=Event.State.ACTIVE)
        event_locked = ThreadEvent()
        release_event = ThreadEvent()

        def complete_event():
            close_old_connections()
            with transaction.atomic():
                event = Event.objects.select_for_update().get(pk=self.event.pk)
                event.state = Event.State.COMPLETED
                event.save(update_fields=("state", "updated_at"))
                event_locked.set()
                release_event.wait(timeout=5)
            close_old_connections()

        thread = Thread(target=complete_event)
        thread.start()
        self.assertTrue(event_locked.wait(timeout=5))
        release_event.set()
        service = EventRegistrationService(
            authority=ContributionAuthority(Capability())
        )
        with self.assertRaises(InvalidEventRegistration):
            service.register(
                identity=self.user,
                registration_id="registration:completion-race",
                event_id=self.event.event_id,
                participant=self.profile,
                evidence_reference="evidence:completion-race",
                eligibility_basis_type=(
                    EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION
                ),
                eligibility_basis_reference="event-state:active",
                occurred_at=NOW,
            )
        thread.join()
        self.assertEqual(EventRegistration.objects.count(), 0)

    def test_constraint_failure_rolls_back_to_savepoint(self):
        registration = EventRegistrationService(
            authority=ContributionAuthority(Capability())
        ).register(
            identity=self.user,
            registration_id="registration:savepoint",
            event_id=self.event.event_id,
            participant=self.profile,
            evidence_reference="evidence:savepoint",
            eligibility_basis_type=(
                EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION
            ),
            eligibility_basis_reference="event-state:published",
            occurred_at=NOW,
        )

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    EventRegistration.objects.filter(pk=registration.pk).update(
                        predecessor_id=registration.pk
                    )
            self.assertEqual(
                EventRegistration.objects.get(pk=registration.pk).state,
                EventRegistration.State.REGISTERED,
            )

    def test_cancellation_racing_re_registration_serializes_successor(self):
        cancel_locked = ThreadEvent()
        release_cancel = ThreadEvent()
        re_registration_started = ThreadEvent()
        result_lock = Lock()
        results = {}

        class BlockingCancellationCapability:
            def authorise(self, *, identity, action, target, timestamp):
                if action == EventRegistrationTransition.ActionType.CANCEL:
                    cancel_locked.set()
                    release_cancel.wait(timeout=5)
                return f"authority:{identity.pk}:{action}"

        service = EventRegistrationService(
            authority=ContributionAuthority(BlockingCancellationCapability())
        )
        predecessor = service.register(
            identity=self.user,
            registration_id="registration:cancel-race:predecessor",
            event_id=self.event.event_id,
            participant=self.profile,
            evidence_reference="evidence:cancel-race:predecessor",
            eligibility_basis_type=(
                EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION
            ),
            eligibility_basis_reference="event-state:published",
            occurred_at=NOW,
        )

        def cancel():
            close_old_connections()
            try:
                result = service.cancel(
                    identity=User.objects.get(pk=self.user.pk),
                    registration_id=predecessor.registration_id,
                    cancellation_basis="administrative",
                    basis_source="actor_recorded",
                    occurred_at=NOW,
                )
            except Exception as error:
                result = error
            finally:
                close_old_connections()
            with result_lock:
                results["cancel"] = result

        def re_register():
            close_old_connections()
            re_registration_started.set()
            try:
                result = service.register(
                    identity=User.objects.get(pk=self.user.pk),
                    registration_id="registration:cancel-race:successor",
                    event_id=self.event.event_id,
                    participant=Identity.objects.get(pk=self.profile.pk),
                    evidence_reference="evidence:cancel-race:successor",
                    eligibility_basis_type=(
                        EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION
                    ),
                    eligibility_basis_reference="event-state:published",
                    predecessor=EventRegistration.objects.get(pk=predecessor.pk),
                    occurred_at=NOW,
                )
            except Exception as error:
                result = error
            finally:
                close_old_connections()
            with result_lock:
                results["re_register"] = result

        cancel_thread = Thread(target=cancel)
        cancel_thread.start()
        self.assertTrue(cancel_locked.wait(timeout=5))
        re_registration_thread = Thread(target=re_register)
        re_registration_thread.start()
        self.assertTrue(re_registration_started.wait(timeout=5))
        release_cancel.set()
        cancel_thread.join()
        re_registration_thread.join()

        self.assertIsInstance(
            results["cancel"],
            EventRegistrationTransition,
        )
        self.assertIsInstance(results["re_register"], EventRegistration)
        predecessor.refresh_from_db()
        successor = results["re_register"]
        self.assertEqual(predecessor.state, EventRegistration.State.CANCELLED)
        self.assertEqual(successor.predecessor_id, predecessor.pk)
        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.event,
                participant=self.profile,
                state=EventRegistration.State.REGISTERED,
            ).count(),
            1,
        )

    def test_named_constraints_are_present_in_postgresql_catalog(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE indexname IN (
                    'one_active_event_registration',
                    'one_event_registration_successor',
                    'unique_event_reg_idempotency'
                )
                """
            )
            index_names = {row[0] for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conname IN (
                    'event_registration_not_self_predecessor',
                    'unique_event_registration_evidence'
                )
                """
            )
            constraint_names = {row[0] for row in cursor.fetchall()}

        self.assertEqual(
            index_names,
            {
                "one_active_event_registration",
                "one_event_registration_successor",
                "unique_event_reg_idempotency",
            },
        )
        self.assertEqual(
            constraint_names,
            {
                "event_registration_not_self_predecessor",
                "unique_event_registration_evidence",
            },
        )

    def test_registration_migration_reverses_and_reapplies(self):
        executor = MigrationExecutor(connection)
        received_leaf_targets = tuple(executor.loader.graph.leaf_nodes())
        try:
            executor.migrate([("core", "0009_care_response_foundation")])
            self.assertNotIn(
                "core_eventregistration",
                connection.introspection.table_names(),
            )
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(received_leaf_targets)
        self.assertIn(
            "core_eventregistration",
            connection.introspection.table_names(),
        )
        self.assertIn(
            "core_eventattendance",
            connection.introspection.table_names(),
        )
