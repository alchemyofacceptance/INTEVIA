from datetime import datetime, timezone
from threading import Event as ThreadEvent, Thread
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from core.models import (
    Contribution,
    Identity,
    IdentityTransition,
    ProfileRole,
    Role,
)
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.contribution_service import ContributionService
from src.intevia.services.identity_service import IdentityLifecycleService


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL Contribution locking guardian",
)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)

    def authorise(self, *, identity, action, target, timestamp):
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}"


class PausingCapability:
    def __init__(self, entered, release):
        self.entered = entered
        self.release = release

    def authorise(self, *, identity, action, target, timestamp):
        self.entered.set()
        self.release.wait(timeout=5)
        return f"authority:{identity.pk}:{action}"


@POSTGRESQL_ONLY
class ContributionPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(username="postgres-contribution")
        self.profile = Identity.objects.create(credential=self.user, access_state=Identity.AccessState.ACTIVE)
        role = Role.objects.create(pk=1_000_002, name="PostgreSQL contribution actor")
        ProfileRole.objects.create(identity=self.profile, role=role)
        self.service = ContributionService(
            authority=ContributionAuthority(Capability())
        )

    def create_contribution(self, contribution_id="contribution:postgres-locking"):
        return self.service.create_contribution(
            identity=self.user,
            contribution_id=contribution_id,
            content="governed content",
            occurred_at=NOW,
        )

    def test_nullable_current_version_does_not_prevent_contribution_lock(self):
        contribution = Contribution.objects.create(
            contribution_id="contribution:postgres-null-version",
            contributor=self.profile,
        )

        with transaction.atomic():
            locked = self.service._locked(contribution.contribution_id)
            self.assertIsNone(locked.current_version_id)
            self.assertEqual(locked.state, Contribution.State.DRAFT)

    def test_current_version_context_preserves_lifecycle_and_lineage(self):
        contribution = self.create_contribution()

        transition = self.service.submit_contribution(
            identity=self.user,
            contribution_id=contribution.contribution_id,
            occurred_at=NOW,
        )

        contribution.refresh_from_db()
        self.assertEqual(contribution.state, Contribution.State.SUBMITTED)
        self.assertEqual(transition.version_id, contribution.current_version_id)
        self.assertEqual(
            transition.authority_reference,
            "authority:1:submit_contribution",
        )
        self.assertEqual(contribution.transitions.count(), 2)

    def test_competing_transition_waits_for_contribution_aggregate_lock(self):
        contribution = self.create_contribution(
            "contribution:postgres-lock-serialization"
        )
        lock_acquired = ThreadEvent()
        release_lock = ThreadEvent()
        transition_started = ThreadEvent()
        transition_finished = ThreadEvent()
        results = []

        def hold_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    ContributionService._locked(contribution.contribution_id)
                    lock_acquired.set()
                    release_lock.wait(timeout=5)
            finally:
                close_old_connections()

        def submit():
            close_old_connections()
            transition_started.set()
            try:
                result = self.service.submit_contribution(
                    identity=User.objects.get(pk=self.user.pk),
                    contribution_id=contribution.contribution_id,
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
        transition_thread = Thread(target=submit)
        transition_thread.start()
        self.assertTrue(transition_started.wait(timeout=5))
        self.assertFalse(transition_finished.wait(timeout=0.2))
        release_lock.set()
        lock_thread.join()
        transition_thread.join()

        self.assertEqual(len(results), 1)
        self.assertNotIsInstance(results[0], Exception)
        contribution.refresh_from_db()
        self.assertEqual(contribution.state, Contribution.State.SUBMITTED)

    def test_denied_transition_preserves_state_and_lineage(self):
        contribution = self.create_contribution("contribution:postgres-denied")
        denied = ContributionService(
            authority=ContributionAuthority(Capability({"submit_contribution"}))
        )
        transition_count = contribution.transitions.count()

        with self.assertRaises(NotAuthorised):
            denied.submit_contribution(
                identity=self.user,
                contribution_id=contribution.contribution_id,
                occurred_at=NOW,
            )

        contribution.refresh_from_db()
        self.assertEqual(contribution.state, Contribution.State.DRAFT)
        self.assertEqual(contribution.transitions.count(), transition_count)

    def test_deactivation_during_authority_evaluation_fails_closed(self):
        contribution = self.create_contribution(
            "contribution:postgres-deactivation-race"
        )
        transition_count = contribution.transitions.count()
        capability_entered = ThreadEvent()
        release_capability = ThreadEvent()
        result = []
        service = ContributionService(
            authority=ContributionAuthority(
                PausingCapability(capability_entered, release_capability)
            )
        )

        def submit():
            close_old_connections()
            try:
                result.append(
                    service.submit_contribution(
                        identity=User.objects.get(pk=self.user.pk),
                        contribution_id=contribution.contribution_id,
                        occurred_at=NOW,
                    )
                )
            except Exception as error:
                result.append(error)
            finally:
                close_old_connections()

        submit_thread = Thread(target=submit)
        submit_thread.start()
        self.assertTrue(capability_entered.wait(timeout=5))
        IdentityLifecycleService.change_access(
            identity_id=self.profile.identity_id,
            action=IdentityTransition.Action.DEACTIVATE,
            requesting_actor=None,
            authority_reference="authority:deactivate",
            technical_executor="postgres-guardian",
            evidence_reference="evidence:deactivate",
            reason_category=IdentityTransition.ReasonCategory.SECURITY,
            correlation_id=uuid4(),
            occurred_at=NOW,
        )
        release_capability.set()
        submit_thread.join(timeout=5)

        self.assertFalse(submit_thread.is_alive())
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], NotAuthorised)
        contribution.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(contribution.state, Contribution.State.DRAFT)
        self.assertEqual(contribution.transitions.count(), transition_count)
        self.assertEqual(
            self.profile.access_state,
            Identity.AccessState.DEACTIVATED,
        )
