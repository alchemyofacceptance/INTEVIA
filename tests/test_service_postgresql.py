from datetime import datetime, timezone
from threading import Event as ThreadEvent, Thread
from unittest import skipUnless

from django.contrib.auth.models import User
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from core.models import Profile, ProfileRole, Role, Service
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.service_service import GovernedService


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL Service locking guardian",
)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)

    def authorise(self, *, identity, action, target, timestamp):
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}"


@POSTGRESQL_ONLY
class ServicePostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(username="postgres-service")
        self.profile = Profile.objects.create(user=self.user)
        role = Role.objects.create(pk=1_000_001, name="PostgreSQL service actor")
        ProfileRole.objects.create(profile=self.profile, role=role)
        self.foundation = GovernedService(
            authority=ContributionAuthority(Capability())
        )

    def create_service(self, service_id="service:postgres-locking"):
        return self.foundation.create_service(
            identity=self.user,
            service_id=service_id,
            capability_purpose="Governed pathway",
            domain_intent="Serve intent",
            evidence_reference=f"evidence:{service_id}:creation",
            occurred_at=NOW,
        )

    def test_nullable_current_version_does_not_prevent_service_lock(self):
        service = Service.objects.create(
            service_id="service:postgres-null-version",
            created_by=self.profile,
            created_at=NOW,
        )

        with transaction.atomic():
            locked = self.foundation._locked(service.service_id)
            self.assertIsNone(locked.current_version_id)
            self.assertEqual(locked.state, Service.State.DRAFT)

    def test_current_version_context_preserves_lifecycle_and_evidence(self):
        service = self.create_service()

        transition = self.foundation.transition_service(
            identity=self.user,
            service_id=service.service_id,
            command="publish_service",
            evidence_reference="evidence:service:publish",
            occurred_at=NOW,
        )

        service.refresh_from_db()
        self.assertEqual(service.state, Service.State.PUBLISHED)
        self.assertEqual(transition.version_id, service.current_version_id)
        self.assertEqual(transition.authority_reference, "authority:1:publish_service")
        self.assertTrue(
            service.evidence_references.filter(
                transition=transition,
                reference="evidence:service:publish",
            ).exists()
        )

    def test_competing_transition_waits_for_service_aggregate_lock(self):
        service = self.create_service("service:postgres-lock-serialization")
        lock_acquired = ThreadEvent()
        release_lock = ThreadEvent()
        transition_started = ThreadEvent()
        transition_finished = ThreadEvent()
        results = []

        def hold_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    GovernedService._locked(service.service_id)
                    lock_acquired.set()
                    release_lock.wait(timeout=5)
            finally:
                close_old_connections()

        def publish():
            close_old_connections()
            transition_started.set()
            try:
                result = self.foundation.transition_service(
                    identity=User.objects.get(pk=self.user.pk),
                    service_id=service.service_id,
                    command="publish_service",
                    evidence_reference="evidence:serialized-publish",
                    occurred_at=NOW,
                )
            except Exception as error:
                result = error
            finally:
                transition_finished.set()
                close_old_connections()
            results.append(result)

        lock_thread = Thread(target=hold_lock)
        lock_thread.start()
        self.assertTrue(lock_acquired.wait(timeout=5))
        transition_thread = Thread(target=publish)
        transition_thread.start()
        self.assertTrue(transition_started.wait(timeout=5))
        self.assertFalse(transition_finished.wait(timeout=0.2))
        release_lock.set()
        lock_thread.join()
        transition_thread.join()

        self.assertEqual(len(results), 1)
        self.assertNotIsInstance(results[0], Exception)
        service.refresh_from_db()
        self.assertEqual(service.state, Service.State.PUBLISHED)

    def test_denied_transition_preserves_state_and_lineage(self):
        service = self.create_service("service:postgres-denied")
        denied = GovernedService(
            authority=ContributionAuthority(Capability({"publish_service"}))
        )
        transition_count = service.transitions.count()
        evidence_count = service.evidence_references.count()

        with self.assertRaises(NotAuthorised):
            denied.transition_service(
                identity=self.user,
                service_id=service.service_id,
                command="publish_service",
                evidence_reference="evidence:denied",
                occurred_at=NOW,
            )

        service.refresh_from_db()
        self.assertEqual(service.state, Service.State.DRAFT)
        self.assertEqual(service.transitions.count(), transition_count)
        self.assertEqual(service.evidence_references.count(), evidence_count)