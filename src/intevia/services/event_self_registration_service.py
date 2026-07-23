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
	CREATED = "created"
	EXISTING = "existing"
	UNAVAILABLE = "unavailable"
	ACCOUNT_REFUSAL = "account_refusal"


class EventSelfRegistrationService:
	def __init__(self, policy: PreAlphaSelfRegistrationPolicy | None = None) -> None:
		self.policy = policy or PreAlphaSelfRegistrationPolicy()

	def attempt(
		self,
		*,
		credential: User,
		identity: Identity,
		event_id: str,
	) -> SelfRegistrationOutcome:
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