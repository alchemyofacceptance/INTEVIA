import inspect
import os
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core.models import (
	Event,
	EventAttendance,
	EventRegistration,
	EventRegistrationEvidenceReference,
	EventRegistrationTransition,
	Identity,
	ProfileRole,
	Role,
)
from core.views import (
	REGISTRATION_INDETERMINATE,
	REGISTRATION_NOT_COMPLETED,
	REGISTRATION_OUTCOME_MESSAGES,
)
from src.intevia.services.event_registration_policy import (
	POLICY_ACTION,
	POLICY_IDENTITY,
	POLICY_VERSION,
	PreAlphaSelfRegistrationPolicy,
	RegistrationPolicyBinding,
	RegistrationUnavailable,
)
from src.intevia.services.event_self_registration_service import (
	EventSelfRegistrationService,
	SelfRegistrationOutcome,
)


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


class S010Fixtures:
	def identity(self, username, *, state=Identity.AccessState.ACTIVE):
		user = User.objects.create_user(
			username=username,
			password="governed-test-password",
			is_active=state != Identity.AccessState.DEACTIVATED,
		)
		identity = Identity.objects.create(
			credential=user,
			display_name=username.title(),
			access_state=state,
		)
		role, _ = Role.objects.get_or_create(name="S010 internal pre-alpha actor")
		ProfileRole.objects.create(identity=identity, role=role)
		return user, identity

	def event(self, owner, suffix, *, state=Event.State.PUBLISHED):
		return Event.objects.create(
			event_id=f"event:s010:{suffix}",
			title=f"Event {suffix}",
			description="A governed Event.",
			owner=owner,
			state=state,
			created_at=NOW,
		)

	def policy_environment(self, identity, event):
		return {
			"INTEVIA_S010_POLICY_IDENTITY": POLICY_IDENTITY,
			"INTEVIA_S010_POLICY_VERSION": POLICY_VERSION,
			"INTEVIA_S010_POLICY_ENVIRONMENT": "internal-pre-alpha",
			"INTEVIA_S010_POLICY_ENABLED": "true",
			"INTEVIA_S010_POLICY_EFFECTIVE_AT": "2026-01-01T00:00:00Z",
			"INTEVIA_S010_POLICY_EXPIRES_AT": "2027-01-01T00:00:00Z",
			"INTEVIA_S010_POLICY_REVOKED": "false",
			"INTEVIA_S010_POLICY_IDENTITY_IDS": str(identity.identity_id),
			"INTEVIA_S010_POLICY_EVENT_IDS": event.event_id,
		}


class S010PolicyTests(S010Fixtures, TestCase):
	def setUp(self):
		self.user, self.identity_record = self.identity("s010-policy")
		self.event_record = self.event(self.identity_record, "policy")

	def test_policy_binding_fails_closed_for_every_governing_state(self):
		binding = RegistrationPolicyBinding.from_environment()
		self.assertFalse(binding.enabled)
		self.assertIsNone(binding.effective_at)
		self.assertIsNone(binding.expires_at)

		class Target:
			event = self.event_record
			participant = self.identity_record
			origin = EventRegistration.Origin.SELF
			predecessor = None
			correlation_identity = "00000000-0000-4000-8000-000000000001"

		with self.assertRaises(RegistrationUnavailable):
			PreAlphaSelfRegistrationPolicy(binding).authorise(
				identity=self.identity_record,
				action=POLICY_ACTION,
				target=Target(),
				timestamp=NOW,
			)

	def test_policy_reference_and_eligibility_are_distinct_and_source_derived(self):
		with patch.dict(
			os.environ,
			self.policy_environment(self.identity_record, self.event_record),
		):
			outcome = EventSelfRegistrationService().attempt(
				credential=self.user,
				identity=self.identity_record,
				event_id=self.event_record.event_id,
			)
		self.assertEqual(outcome, SelfRegistrationOutcome.CREATED)
		registration = EventRegistration.objects.get()
		transition = registration.transitions.get()
		evidence = transition.evidence_references.get()
		correlation = UUID(transition.idempotency_key)
		self.assertEqual(
			transition.authority_reference,
			f"reg-auth-prealpha-001:v1:{correlation}",
		)
		self.assertEqual(
			registration.eligibility_basis_type,
			EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION,
		)
		self.assertEqual(
			registration.eligibility_policy_reference,
			f"{POLICY_IDENTITY}:{POLICY_VERSION}",
		)
		self.assertEqual(evidence.reference, f"self-submission:v1:{correlation}")
		self.assertEqual(registration.eligibility_evaluated_at, transition.occurred_at)


class S010HttpGuardianTests(S010Fixtures, TestCase):
	def setUp(self):
		self.user, self.identity_record = self.identity("s010-http")
		self.other_user, self.other = self.identity("s010-other")
		self.event_record = self.event(self.identity_record, "http")
		self.path = reverse("event-register-self", args=[self.event_record.event_id])
		self.detail = reverse("event-detail", args=[self.event_record.event_id])
		self.environment = self.policy_environment(
			self.identity_record,
			self.event_record,
		)
		self.client.post(
			reverse("login"),
			{"username": self.user.username, "password": "governed-test-password"},
		)

	def post(self, data=None, *, follow=True):
		with patch.dict(os.environ, self.environment):
			return self.client.post(self.path, data or {}, follow=follow)

	def test_g3_g14_g15_g17_g20_success_is_atomic_private_prg(self):
		attendance_before = EventAttendance.objects.count()
		response = self.post()
		self.assertRedirects(response, self.detail)
		self.assertContains(response, "Your registration record has been created.")
		self.assertContains(response, "A current registration record exists for you.")
		self.assertEqual(EventRegistration.objects.count(), 1)
		self.assertEqual(EventRegistrationTransition.objects.count(), 1)
		self.assertEqual(EventRegistrationEvidenceReference.objects.count(), 1)
		self.assertEqual(EventAttendance.objects.count(), attendance_before)
		self.assertIn("private", response.headers["Cache-Control"])
		self.assertIn("no-store", response.headers["Cache-Control"])
		body = response.content.decode()
		for private_value in (
			EventRegistration.objects.get().registration_id,
			EventRegistration.objects.get().transitions.get().authority_reference,
			EventRegistrationEvidenceReference.objects.get().reference,
		):
			self.assertNotIn(private_value, body)

	def test_g5_replay_and_g6_two_tab_duplicate_create_one_outcome(self):
		correlation = UUID("00000000-0000-4000-8000-000000000010")
		with patch(
			"src.intevia.services.event_self_registration_service.uuid4",
			return_value=correlation,
		):
			first = self.post()
			second = self.post()
		self.assertContains(first, "Your registration record has been created.")
		self.assertContains(second, "Your registration record has been created.")
		self.assertEqual(EventRegistration.objects.count(), 1)
		self.assertEqual(EventRegistrationTransition.objects.count(), 1)
		self.assertEqual(EventRegistrationEvidenceReference.objects.count(), 1)

		third = self.post()
		self.assertContains(
			third,
			"A current registration record already exists for you.",
		)
		self.assertEqual(EventRegistrationEvidenceReference.objects.count(), 1)

	def test_g7_injected_subject_and_eligibility_input_are_ignored(self):
		response = self.post(
			{
				"participant": self.other.pk,
				"eligibility_basis_type": "other",
				"eligibility_basis_reference": "human-supplied",
				"eligibility_evaluated_at": "1900-01-01T00:00:00Z",
			}
		)
		self.assertEqual(response.status_code, 200)
		registration = EventRegistration.objects.get()
		self.assertEqual(registration.participant, self.identity_record)
		self.assertNotEqual(registration.eligibility_basis_type, "other")
		self.assertNotIn("human-supplied", registration.eligibility_basis_reference)

	def test_g8_session_failures_create_no_residue(self):
		self.client.post(reverse("logout"))
		response = self.client.post(self.path)
		self.assertEqual(response.status_code, 302)
		self.assertEqual(EventRegistration.objects.count(), 0)

		self.client.post(
			reverse("login"),
			{"username": self.user.username, "password": "governed-test-password"},
		)
		Identity.objects.filter(pk=self.identity_record.pk).update(access_epoch=1)
		response = self.client.post(self.path)
		self.assertRedirects(response, reverse("login"))
		self.assertEqual(EventRegistration.objects.count(), 0)

	def test_g9_staff_and_superuser_follow_same_policy(self):
		self.user.is_staff = True
		self.user.is_superuser = True
		self.user.save(update_fields=("is_staff", "is_superuser"))
		self.environment["INTEVIA_S010_POLICY_IDENTITY_IDS"] = str(self.other.identity_id)
		response = self.post()
		self.assertContains(
			response,
			"This registration cannot be completed through this account.",
		)
		self.assertEqual(EventRegistration.objects.count(), 0)

	def assert_absolute_account_refusal(self):
		with patch.dict(os.environ, self.environment):
			outcome = EventSelfRegistrationService().attempt(
				credential=self.user,
				identity=self.identity_record,
				event_id=self.event_record.event_id,
			)
		self.assertEqual(outcome, SelfRegistrationOutcome.ACCOUNT_REFUSAL)
		response = self.post()
		self.assertContains(
			response,
			"This registration cannot be completed through this account.",
		)
		self.assertEqual(EventRegistration.objects.count(), 0)
		self.assertEqual(EventRegistrationTransition.objects.count(), 0)
		self.assertEqual(EventRegistrationEvidenceReference.objects.count(), 0)

	def test_staff_flag_is_absolute_account_refusal_when_allowlisted(self):
		self.user.is_staff = True
		self.user.is_superuser = False
		self.user.save(update_fields=("is_staff", "is_superuser"))

		self.assert_absolute_account_refusal()

	def test_superuser_flag_is_absolute_account_refusal_when_allowlisted(self):
		self.user.is_staff = False
		self.user.is_superuser = True
		self.user.save(update_fields=("is_staff", "is_superuser"))

		self.assert_absolute_account_refusal()

	def test_g10_g11_hidden_and_missing_are_indistinguishable_and_shared(self):
		hidden = self.event(self.other, "hidden")
		hidden_path = reverse("event-register-self", args=[hidden.event_id])
		missing_path = reverse("event-register-self", args=["event:s010:missing"])
		with patch(
			"src.intevia.services.event_self_registration_service.visible_event_queryset",
			wraps=__import__(
				"src.intevia.services.event_read_service",
				fromlist=["visible_event_queryset"],
			).visible_event_queryset,
		) as shared:
			hidden_response = self.client.post(hidden_path)
			missing_response = self.client.post(missing_path)
		self.assertEqual(hidden_response.status_code, 404)
		self.assertEqual(hidden_response.content, missing_response.content)
		self.assertEqual(shared.call_count, 2)

	def test_g3_precommit_failure_has_no_residue_and_confirmed_wording(self):
		with patch.dict(os.environ, self.environment), patch(
			"src.intevia.services.event_registration_policy.PreAlphaSelfRegistrationPolicy.determine_eligibility",
			side_effect=RuntimeError("forced precommit failure"),
		):
			response = self.client.post(self.path, follow=True)
		self.assertContains(response, REGISTRATION_NOT_COMPLETED)
		self.assertEqual(EventRegistration.objects.count(), 0)
		self.assertEqual(EventRegistrationTransition.objects.count(), 0)
		self.assertEqual(EventRegistrationEvidenceReference.objects.count(), 0)

	def test_g12_postcommit_response_failure_is_indeterminate_and_durable(self):
		with patch.dict(os.environ, self.environment), patch(
			"core.views._registration_success_response",
			side_effect=RuntimeError("forced response failure"),
		):
			response = self.client.post(self.path)
		self.assertEqual(response.status_code, 500)
		self.assertContains(
			response,
			REGISTRATION_INDETERMINATE,
			status_code=500,
		)
		self.assertNotContains(
			response,
			REGISTRATION_NOT_COMPLETED,
			status_code=500,
		)
		self.assertEqual(EventRegistration.objects.count(), 1)

	def test_g13_no_deferred_execution_touches_registration(self):
		from src.intevia.services import (
			event_registration_policy,
			event_self_registration_service,
		)

		source = inspect.getsource(event_registration_policy) + inspect.getsource(
			event_self_registration_service
		)
		for forbidden in ("celery", "delay(", "enqueue", "schedule", "retry"):
			self.assertNotIn(forbidden, source.lower())

	def test_g16_csrf_is_enforced_and_get_is_405(self):
		csrf_client = Client(enforce_csrf_checks=True)
		csrf_client.post(
			reverse("login"),
			{"username": self.user.username, "password": "governed-test-password"},
		)
		self.assertEqual(csrf_client.post(self.path).status_code, 403)
		self.assertEqual(self.client.get(self.path).status_code, 405)

	def test_g17_refresh_of_redirect_target_does_not_resubmit(self):
		response = self.post(follow=False)
		self.assertEqual(response.status_code, 302)
		self.client.get(response.url)
		self.client.get(response.url)
		self.assertEqual(EventRegistration.objects.count(), 1)

	def test_g18_frozen_phrases_and_forbidden_vocabulary(self):
		phrases = set(REGISTRATION_OUTCOME_MESSAGES.values()) | {
			REGISTRATION_NOT_COMPLETED,
			REGISTRATION_INDETERMINATE,
		}
		self.assertEqual(
			phrases,
			{
				"A current registration record already exists for you.",
				"Registration is not available through this surface for this Event.",
				"This registration cannot be completed through this account.",
				"We could not complete the registration right now. No registration was created.",
				"We could not confirm the result. Return to the Event page to check the current registration record.",
				"Your registration record has been created.",
			},
		)
		body = self.client.get(self.detail).content.decode().lower()
		for forbidden in (
			"booking",
			"reservation",
			"approval",
			"pending",
			"queue",
			"entitlement",
			"guaranteed place",
			"submitted",
			"received",
			"under review",
		):
			self.assertNotIn(forbidden, body)

	def test_unavailable_event_configuration_uses_only_safe_phrase(self):
		self.environment["INTEVIA_S010_POLICY_EVENT_IDS"] = "event:s010:other"
		response = self.post()
		self.assertContains(
			response,
			"Registration is not available through this surface for this Event.",
		)
		self.assertEqual(EventRegistration.objects.count(), 0)