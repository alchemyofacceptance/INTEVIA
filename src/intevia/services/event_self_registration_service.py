"""Authenticated direct self-registration journey for S010."""

from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from django.contrib.auth.models import User

from core.models import Event, Identity
from src.intevia.services.contribution_authority import (
	ContributionAuthority,
	NotAuthorised,
)
from src.intevia.services.event_read_service import (
	EventNotVisible,
	visible_event_queryset,
)
from src.intevia.services.event_registration_policy import (
	PreAlphaSelfRegistrationPolicy,
	RegistrationAccountRefused,
	RegistrationUnavailable,
)
from src.intevia.services.event_registration_service import (
	DuplicateActiveEventRegistration,
	EventRegistrationService,
	InvalidEventRegistration,
)


class SelfRegistrationOutcome(StrEnum):
	"""The four results the self-registration journey reports: created,
	existing (already registered), unavailable, account refusal."""
	CREATED = "created"
	EXISTING = "existing"
	UNAVAILABLE = "unavailable"
	ACCOUNT_REFUSAL = "account_refusal"


class EventSelfRegistrationService:
	"""Adapt the S010 authenticated view to governed self-registration.

	Check Event visibility for the supplied Identity, then use
	EventRegistrationService with the selected policy as both authority
	capability and eligibility source. By default this is the bounded
	internal-pre-alpha policy, including its owner-only restriction.
	Return a SelfRegistrationOutcome for handled results; other failures
	propagate. Authentication and credential/Identity selection belong
	to the caller and delegated services, not this class alone.
	"""
	def __init__(self, policy: PreAlphaSelfRegistrationPolicy | None = None) -> None:
		"""Keep the given policy, or build one from the environment."""
		self.policy = policy or PreAlphaSelfRegistrationPolicy()

	def attempt(
		self,
		*,
		credential: User,
		identity: Identity,
		event_id: str,
	) -> SelfRegistrationOutcome:
		"""Attempt registration and map handled results for the Identity.

		First require the Event to be visible through ownership or an
		existing registration, participation or attendance relationship.
		Missing or ambiguous visible lookup raises EventNotVisible.
		Construct the delegated service with this policy for authority and
		eligibility, using a fresh UUID for registration id, evidence and
		idempotency key, and no predecessor.

		Map DuplicateActiveEventRegistration to EXISTING;
		RegistrationUnavailable or InvalidEventRegistration to UNAVAILABLE;
		RegistrationAccountRefused or NotAuthorised to ACCOUNT_REFUSAL.
		Any normal register return gives CREATED; the returned row is not
		inspected to distinguish creation from replay.

		Other exceptions propagate, including Django ValidationError and
		InvalidRegistrationPredecessor. This method does not create a shared
		transaction around visibility and registration, directly compare
		credential with identity, or reuse a request key across attempts.
		Event state and existing-row checks can precede policy evaluation.
		"""
		try:
			visible_event_queryset(identity).get(event_id=event_id)
		except (Event.DoesNotExist, Event.MultipleObjectsReturned) as exc:
			raise EventNotVisible("resource is not visible") from exc

		correlation_id = uuid4()
		service = EventRegistrationService(
			authority=ContributionAuthority(self.policy),
			eligibility=self.policy,
		)
		try:
			service.register(
				identity=credential,
				registration_id=f"registration:{correlation_id}",
				event_id=event_id,
				participant=identity,
				evidence_reference=f"self-submission:v1:{correlation_id}",
				eligibility_basis_type=None,
				eligibility_basis_reference=None,
				idempotency_key=str(correlation_id),
			)
		except DuplicateActiveEventRegistration:
			return SelfRegistrationOutcome.EXISTING
		except (RegistrationUnavailable, InvalidEventRegistration):
			return SelfRegistrationOutcome.UNAVAILABLE
		except (RegistrationAccountRefused, NotAuthorised):
			return SelfRegistrationOutcome.ACCOUNT_REFUSAL
		return SelfRegistrationOutcome.CREATED


__all__ = ["EventSelfRegistrationService", "SelfRegistrationOutcome"]