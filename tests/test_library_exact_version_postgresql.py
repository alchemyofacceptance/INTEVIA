from datetime import datetime, timedelta, timezone
from threading import Barrier, Event as ThreadEvent, Lock, Thread

from django.contrib.auth.models import User
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.models import Event, EventRegistration, Identity, LibraryResource, LibraryResourceVersion
from src.intevia.services.library_exact_version_contract import (
    AuthorityResult,
    BindingDecision,
    BindingKind,
    BindingSnapshot,
    LibraryAction,
    LibraryExactVersionContract,
    LibraryRequestContext,
    LinkabilityResult,
    POLICY_ENVIRONMENT,
    POLICY_REFERENCE,
)
from src.intevia.services.library_exact_version_policy import ImmutableLibraryBindingProvider, LibraryExactVersionPolicy


NOW = datetime(2026, 7, 23, 18, 30, tzinfo=timezone.utc)


class LibraryExactVersionPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        if connection.vendor != "postgresql":
            self.fail("mandatory S011-A PostgreSQL guardians require PostgreSQL")
        self.user = User.objects.create_user(username="s011a-postgresql")
        self.identity = Identity.objects.create(
            credential=self.user,
            access_state=Identity.AccessState.ACTIVE,
            access_epoch=9,
        )
        other_user = User.objects.create_user(username="s011a-postgresql-other")
        self.other_identity = Identity.objects.create(
            credential=other_user,
            access_state=Identity.AccessState.ACTIVE,
        )
        self.resource = LibraryResource.objects.create(
            resource_id="lib.resource~postgresql",
            created_by=self.identity,
            state=LibraryResource.State.PUBLISHED,
            created_at=NOW,
        )
        self.version = LibraryResourceVersion.objects.create(
            resource=self.resource,
            version_number=1,
            content="postgresql governed content",
            created_by=self.identity,
            created_at=NOW,
        )
        self.event = Event.objects.create(
            event_id="event:s011a:postgresql",
            title="S011-A PostgreSQL",
            owner=self.identity,
            state=Event.State.PUBLISHED,
            created_at=NOW,
        )
        for number, participant in enumerate((self.identity, self.other_identity), start=1):
            EventRegistration.objects.create(
                registration_id=f"registration:s011a:postgresql:{number}",
                event=self.event,
                participant=participant,
                state=EventRegistration.State.REGISTERED,
                origin=EventRegistration.Origin.THIRD_PARTY,
                event_state_at_registration=Event.State.PUBLISHED,
                eligibility_basis_type=EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION,
                eligibility_basis_reference="s011a:test-only",
                eligibility_evaluated_at=NOW,
                registered_at=NOW,
            )
        reference = "lib-authority-binding:postgresql.action:v1"
        snapshot = BindingSnapshot(
            binding_reference=reference,
            binding_version="1",
            policy_reference=POLICY_REFERENCE,
            environment=POLICY_ENVIRONMENT,
            binding_kind=BindingKind.ACTION,
            subject_identity_id=str(self.identity.identity_id),
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference="lib-binding-snapshot:sha256:" + "e" * 64,
            decision=BindingDecision.ALLOW,
            action=LibraryAction.CREATE,
            resource_id=self.resource.resource_id,
            version_number="1",
            viewer_scope=None,
        )
        provider = ImmutableLibraryBindingProvider((snapshot,), enabled=True, complete_for_policy=True)
        self.service = LibraryExactVersionContract(policy=LibraryExactVersionPolicy(provider=provider))
        self.context = LibraryRequestContext(
            request_reference="request.postgresql",
            consumer_reference="consumer.s011b",
            authority_binding_reference=reference,
            policy_reference=POLICY_REFERENCE,
            requested_at=NOW,
        )

    def compose(self):
        scope = self.service.acquire_consequential_library_scope(
            resource_id=self.resource.resource_id,
            version_number=1,
        )
        Event.objects.select_for_update().get(pk=self.event.pk)
        relationship_ids = list(
            EventRegistration.objects.select_for_update()
            .filter(event_id=self.event.pk)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        evidence = self.service.evaluate_consequential_library_truth(
            scope=scope,
            actor_identity_id=self.identity.identity_id,
            action=LibraryAction.CREATE,
            context=self.context,
            evaluated_at=NOW,
        )
        return relationship_ids, evidence

    def test_resource_is_locked_without_locking_immutable_version(self):
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            self.service.acquire_consequential_library_scope(
                resource_id=self.resource.resource_id,
                version_number=1,
            )
        locking_queries = [query["sql"].lower() for query in queries if "for update" in query["sql"].lower()]
        self.assertTrue(any("core_libraryresource" in query for query in locking_queries))
        self.assertFalse(any("core_libraryresourceversion" in query and "for update" in query for query in locking_queries))

    def test_library_state_race_serializes_behind_scope_lock(self):
        scope_locked = ThreadEvent()
        release_scope = ThreadEvent()
        transition_finished = ThreadEvent()
        results = []

        def hold_scope():
            close_old_connections()
            try:
                with transaction.atomic():
                    self.service.acquire_consequential_library_scope(
                        resource_id=self.resource.resource_id,
                        version_number=1,
                    )
                    scope_locked.set()
                    release_scope.wait(timeout=5)
            finally:
                close_old_connections()

        def archive():
            close_old_connections()
            try:
                with transaction.atomic():
                    resource = LibraryResource.objects.select_for_update().get(pk=self.resource.pk)
                    resource.state = LibraryResource.State.ARCHIVED
                    resource.save(update_fields=("state", "updated_at"))
                results.append("archived")
            except Exception as error:
                results.append(error)
            finally:
                transition_finished.set()
                close_old_connections()

        holder = Thread(target=hold_scope)
        holder.start()
        self.assertTrue(scope_locked.wait(timeout=5))
        transition = Thread(target=archive)
        transition.start()
        self.assertFalse(transition_finished.wait(timeout=0.2))
        release_scope.set()
        holder.join(timeout=10)
        transition.join(timeout=10)
        self.assertFalse(holder.is_alive() or transition.is_alive())
        self.assertEqual(results, ["archived"])

    def test_event_race_waits_then_observes_event_before_identity(self):
        event_locked = ThreadEvent()
        release_event = ThreadEvent()
        composition_finished = ThreadEvent()
        results = []

        def hold_event():
            close_old_connections()
            try:
                with transaction.atomic():
                    Event.objects.select_for_update().get(pk=self.event.pk)
                    event_locked.set()
                    release_event.wait(timeout=5)
            finally:
                close_old_connections()

        def compose_relationship():
            close_old_connections()
            try:
                with transaction.atomic():
                    results.append(self.compose())
            except Exception as error:
                results.append(error)
            finally:
                composition_finished.set()
                close_old_connections()

        event_thread = Thread(target=hold_event)
        event_thread.start()
        self.assertTrue(event_locked.wait(timeout=5))
        relationship_thread = Thread(target=compose_relationship)
        relationship_thread.start()
        self.assertFalse(composition_finished.wait(timeout=0.2))
        release_event.set()
        event_thread.join(timeout=10)
        relationship_thread.join(timeout=10)
        self.assertFalse(event_thread.is_alive() or relationship_thread.is_alive())
        self.assertEqual(len(results), 1)
        relationship_ids, evidence = results[0]
        self.assertEqual(relationship_ids, sorted(relationship_ids))
        self.assertEqual(evidence.authority_envelope.payload.result, AuthorityResult.QUALIFIED)

    def test_two_relationship_actions_serialize_without_deadlock(self):
        barrier = Barrier(2)
        result_lock = Lock()
        results = []

        def invoke():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                with transaction.atomic():
                    result = self.compose()
            except Exception as error:
                result = error
            finally:
                close_old_connections()
            with result_lock:
                results.append(result)

        threads = [Thread(target=invoke) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(any(isinstance(result, Exception) for result in results), results)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result[1].linkability_envelope.payload.result == LinkabilityResult.LINKABLE for result in results))

    def test_transaction_rollback_leaves_no_partial_event_mutation(self):
        original_title = self.event.title
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                relationship_ids, evidence = self.compose()
                self.assertEqual(relationship_ids, sorted(relationship_ids))
                self.assertEqual(evidence.authority_envelope.payload.result, AuthorityResult.QUALIFIED)
                Event.objects.filter(pk=self.event.pk).update(title="must roll back")
                raise RuntimeError("test rollback")
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, original_title)