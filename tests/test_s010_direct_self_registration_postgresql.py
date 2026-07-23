import os
from datetime import datetime, timezone
from threading import Barrier, Event as ThreadEvent, Lock, Thread
from unittest import skipUnless
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth.models import User
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.urls import reverse

from core.models import (
	Event,
	EventRegistration,
	EventRegistrationEvidenceReference,
	EventRegistrationTransition,
	Identity,
	ProfileRole,
	Role,
)
from core.views import REGISTRATION_INDETERMINATE
from src.intevia.services.event_registration_policy import (
	POLICY_IDENTITY,
	POLICY_VERSION,
	PreAlphaSelfRegistrationPolicy,
	RegistrationPolicyBinding,
)
from src.intevia.services.event_self_registration_service import (
	EventSelfRegistrationService,
	SelfRegistrationOutcome,
)


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
POSTGRESQL_ONLY = skipUnless(
	connection.vendor == "postgresql",
	"PostgreSQL S010 closure guardian",
)


@POSTGRESQL_ONLY
class S010PostgreSQLGuardianTests(TransactionTestCase):
	reset_sequences = True

	def setUp(self):
		self.user = User.objects.create_user(
			username="s010-postgresql",
			password="governed-test-password",
		)
		self.identity_record = Identity.objects.create(
			credential=self.user,
			display_name="S010 PostgreSQL",
			access_state=Identity.AccessState.ACTIVE,
		)
		role = Role.objects.create(pk=1_000_010, name="S010 PostgreSQL actor")
		ProfileRole.objects.create(identity=self.identity_record, role=role)
		self.event_record = Event.objects.create(
			event_id="event:s010:postgresql",
			title="S010 PostgreSQL",
			owner=self.identity_record,
			state=Event.State.PUBLISHED,
			created_at=NOW,
		)
		self.binding = RegistrationPolicyBinding(
			identity=POLICY_IDENTITY,
			version=POLICY_VERSION,
			environment="internal-pre-alpha",
			enabled=True,
			effective_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
			expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
			revoked=False,
			superseded_by=None,
			identity_ids=frozenset({str(self.identity_record.identity_id)}),
			event_ids=frozenset({self.event_record.event_id}),
		)

	def attempt(self):
		close_old_connections()
		try:
			return EventSelfRegistrationService(
				PreAlphaSelfRegistrationPolicy(self.binding)
			).attempt(
				credential=User.objects.get(pk=self.user.pk),
				identity=Identity.objects.get(pk=self.identity_record.pk),
				event_id=self.event_record.event_id,
			)
		finally:
			close_old_connections()

	def concurrent_attempts(self, *, correlation=None):
		barrier = Barrier(2)
		result_lock = Lock()
		results = []

		def run():
			barrier.wait()
			try:
				result = self.attempt()
			except Exception as error:
				result = error
			with result_lock:
				results.append(result)

		context = (
			patch(
				"src.intevia.services.event_self_registration_service.uuid4",
				return_value=correlation,
			)
			if correlation is not None
			else patch.dict({}, {})
		)
		with context:
			threads = [Thread(target=run) for _ in range(2)]
			for thread in threads:
				thread.start()
			for thread in threads:
				thread.join()
		return results

	def test_g1_concurrent_different_keys_create_one_and_report_existing(self):
		results = self.concurrent_attempts()
		self.assertCountEqual(
			results,
			[SelfRegistrationOutcome.CREATED, SelfRegistrationOutcome.EXISTING],
		)
		self.assertEqual(EventRegistration.objects.count(), 1)
		self.assertEqual(EventRegistrationTransition.objects.count(), 1)
		self.assertEqual(EventRegistrationEvidenceReference.objects.count(), 1)

	def test_g2_event_state_race_cannot_register_after_completion(self):
		event_locked = ThreadEvent()
		release_event = ThreadEvent()

		def complete_event():
			close_old_connections()
			with transaction.atomic():
				event = Event.objects.select_for_update().get(pk=self.event_record.pk)
				event.state = Event.State.COMPLETED
				event.save(update_fields=("state", "updated_at"))
				event_locked.set()
				release_event.wait(timeout=5)
			close_old_connections()

		thread = Thread(target=complete_event)
		thread.start()
		self.assertTrue(event_locked.wait(timeout=5))
		release_event.set()
		result = self.attempt()
		thread.join()
		self.assertEqual(result, SelfRegistrationOutcome.UNAVAILABLE)
		self.assertEqual(EventRegistration.objects.count(), 0)

	def test_g4_concurrent_same_key_replays_one_complete_outcome(self):
		results = self.concurrent_attempts(
			correlation=UUID("00000000-0000-4000-8000-000000000004")
		)
		self.assertEqual(results, [SelfRegistrationOutcome.CREATED] * 2)
		self.assertEqual(EventRegistration.objects.count(), 1)
		self.assertEqual(EventRegistrationTransition.objects.count(), 1)
		self.assertEqual(EventRegistrationEvidenceReference.objects.count(), 1)

	def test_g12_postcommit_response_failure_preserves_registration(self):
		environment = {
			"INTEVIA_S010_POLICY_IDENTITY": POLICY_IDENTITY,
			"INTEVIA_S010_POLICY_VERSION": POLICY_VERSION,
			"INTEVIA_S010_POLICY_ENVIRONMENT": "internal-pre-alpha",
			"INTEVIA_S010_POLICY_ENABLED": "true",
			"INTEVIA_S010_POLICY_EFFECTIVE_AT": "2026-01-01T00:00:00Z",
			"INTEVIA_S010_POLICY_EXPIRES_AT": "2027-01-01T00:00:00Z",
			"INTEVIA_S010_POLICY_REVOKED": "false",
			"INTEVIA_S010_POLICY_IDENTITY_IDS": str(self.identity_record.identity_id),
			"INTEVIA_S010_POLICY_EVENT_IDS": self.event_record.event_id,
		}
		self.client.post(
			reverse("login"),
			{"username": self.user.username, "password": "governed-test-password"},
		)
		with patch.dict(os.environ, environment), patch(
			"core.views._registration_success_response",
			side_effect=RuntimeError("forced response failure"),
		):
			response = self.client.post(
				reverse("event-register-self", args=[self.event_record.event_id])
			)
		self.assertEqual(response.status_code, 500)
		self.assertContains(
			response,
			REGISTRATION_INDETERMINATE,
			status_code=500,
		)
		self.assertEqual(EventRegistration.objects.count(), 1)