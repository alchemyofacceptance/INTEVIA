from datetime import datetime, timezone
from threading import Event as ThreadEvent, Thread
from unittest import skipUnless

from django.contrib.auth.models import User
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from core.models import LibraryResource, Profile, ProfileRole, Role
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.library_service import LibraryService


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL Library locking guardian",
)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)

    def authorise(self, *, identity, action, target, timestamp):
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}"


@POSTGRESQL_ONLY
class LibraryPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(username="postgres-library")
        self.profile = Profile.objects.create(user=self.user)
        role = Role.objects.create(pk=1_000_003, name="PostgreSQL library actor")
        ProfileRole.objects.create(profile=self.profile, role=role)
        self.service = LibraryService(
            authority=ContributionAuthority(Capability())
        )

    def create_resource(self, resource_id="library:postgres-locking"):
        return self.service.create_resource(
            identity=self.user,
            resource_id=resource_id,
            content="version one",
            evidence_reference=f"evidence:{resource_id}:creation",
            occurred_at=NOW,
        )

    def test_nullable_current_version_does_not_prevent_library_lock(self):
        resource = LibraryResource.objects.create(
            resource_id="library:postgres-null-version",
            created_by=self.profile,
            created_at=NOW,
        )

        with transaction.atomic():
            locked = self.service._locked(resource.resource_id)
            self.assertIsNone(locked.current_version_id)
            self.assertEqual(locked.state, LibraryResource.State.DRAFT)

    def test_current_version_context_preserves_lifecycle_and_evidence(self):
        resource = self.create_resource()

        transition = self.service.transition_resource(
            identity=self.user,
            resource_id=resource.resource_id,
            command="publish_library_resource",
            evidence_reference="evidence:library:publish",
            occurred_at=NOW,
        )

        resource.refresh_from_db()
        self.assertEqual(resource.state, LibraryResource.State.PUBLISHED)
        self.assertEqual(transition.version_id, resource.current_version_id)
        self.assertEqual(
            transition.authority_reference,
            "authority:1:publish_library_resource",
        )
        self.assertTrue(
            resource.evidence_references.filter(
                transition=transition,
                reference="evidence:library:publish",
            ).exists()
        )

    def test_competing_transition_waits_for_library_aggregate_lock(self):
        resource = self.create_resource("library:postgres-lock-serialization")
        lock_acquired = ThreadEvent()
        release_lock = ThreadEvent()
        transition_started = ThreadEvent()
        transition_finished = ThreadEvent()
        results = []

        def hold_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    LibraryService._locked(resource.resource_id)
                    lock_acquired.set()
                    release_lock.wait(timeout=5)
            finally:
                close_old_connections()

        def publish():
            close_old_connections()
            transition_started.set()
            try:
                result = self.service.transition_resource(
                    identity=User.objects.get(pk=self.user.pk),
                    resource_id=resource.resource_id,
                    command="publish_library_resource",
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
        resource.refresh_from_db()
        self.assertEqual(resource.state, LibraryResource.State.PUBLISHED)

    def test_denied_transition_preserves_state_and_lineage(self):
        resource = self.create_resource("library:postgres-denied")
        denied = LibraryService(
            authority=ContributionAuthority(
                Capability({"publish_library_resource"})
            )
        )
        transition_count = resource.transitions.count()
        evidence_count = resource.evidence_references.count()

        with self.assertRaises(NotAuthorised):
            denied.transition_resource(
                identity=self.user,
                resource_id=resource.resource_id,
                command="publish_library_resource",
                evidence_reference="evidence:denied",
                occurred_at=NOW,
            )

        resource.refresh_from_db()
        self.assertEqual(resource.state, LibraryResource.State.DRAFT)
        self.assertEqual(resource.transitions.count(), transition_count)
        self.assertEqual(resource.evidence_references.count(), evidence_count)
