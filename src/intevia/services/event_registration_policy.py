"""REG-AUTH-PREALPHA-001 v1 direct self-registration policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from uuid import UUID

from core.models import Event, EventRegistration, Identity


POLICY_IDENTITY = "REG-AUTH-PREALPHA-001"
POLICY_VERSION = "v1"
POLICY_ACTION = "register_self"
POLICY_ENVIRONMENT = "internal-pre-alpha"
AUTHORITY_REFERENCE_PREFIX = "reg-auth-prealpha-001:v1"
ELIGIBILITY_REFERENCE_PREFIX = "event-configuration:registration:v1"


class RegistrationPolicyDenied(PermissionError):
	pass


class RegistrationAccountRefused(RegistrationPolicyDenied):
	pass


class RegistrationUnavailable(RegistrationPolicyDenied):
	pass


@dataclass(frozen=True, slots=True)
class RegistrationPolicyBinding:
	identity: str
	version: str
	environment: str
	enabled: bool
	effective_at: datetime | None
	expires_at: datetime | None
	revoked: bool
	superseded_by: str | None
	identity_ids: frozenset[str]
	event_ids: frozenset[str]

	@staticmethod
	def _timestamp(value: str | None) -> datetime | None:
		if not value:
			return None
		try:
			parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
		except ValueError:
			return None
		if parsed.tzinfo is None or parsed.utcoffset() is None:
			return None
		return parsed

	@staticmethod
	def _set(value: str | None) -> frozenset[str]:
		return frozenset(item.strip() for item in (value or "").split(",") if item.strip())

	@classmethod
	def from_environment(cls) -> RegistrationPolicyBinding:
		return cls(
			identity=os.environ.get("INTEVIA_S010_POLICY_IDENTITY", ""),
			version=os.environ.get("INTEVIA_S010_POLICY_VERSION", ""),
			environment=os.environ.get("INTEVIA_S010_POLICY_ENVIRONMENT", ""),
			enabled=os.environ.get("INTEVIA_S010_POLICY_ENABLED", "").lower() == "true",
			effective_at=cls._timestamp(os.environ.get("INTEVIA_S010_POLICY_EFFECTIVE_AT")),
			expires_at=cls._timestamp(os.environ.get("INTEVIA_S010_POLICY_EXPIRES_AT")),
			revoked=os.environ.get("INTEVIA_S010_POLICY_REVOKED", "").lower() == "true",
			superseded_by=os.environ.get("INTEVIA_S010_POLICY_SUPERSEDED_BY") or None,
			identity_ids=cls._set(os.environ.get("INTEVIA_S010_POLICY_IDENTITY_IDS")),
			event_ids=cls._set(os.environ.get("INTEVIA_S010_POLICY_EVENT_IDS")),
		)


@dataclass(frozen=True, slots=True)
class TrustedRegistrationEligibility:
	basis_type: str
	basis_reference: str
	policy_reference: str
	evaluated_at: datetime


class PreAlphaSelfRegistrationPolicy:
	"""Evaluate the Human-qualified policy; callers only adapt its result."""

	def __init__(self, binding: RegistrationPolicyBinding | None = None) -> None:
		self.binding = binding or RegistrationPolicyBinding.from_environment()

	def _qualify(self, *, identity: Identity, action: str, target, timestamp: datetime) -> UUID:
		binding = self.binding
		if (
			binding.identity != POLICY_IDENTITY
			or binding.version != POLICY_VERSION
			or binding.environment != POLICY_ENVIRONMENT
			or not binding.enabled
			or binding.revoked
			or binding.superseded_by is not None
			or binding.effective_at is None
			or binding.expires_at is None
			or not (binding.effective_at <= timestamp < binding.expires_at)
		):
			raise RegistrationUnavailable("registration policy unavailable")
		if (
			identity.credential.is_staff
			or identity.credential.is_superuser
			or identity.access_state != Identity.AccessState.ACTIVE
			or str(identity.identity_id) not in binding.identity_ids
			or target.participant.pk != identity.pk
			or target.origin != EventRegistration.Origin.SELF
		):
			raise RegistrationAccountRefused("registration account refused")
		if (
			action != POLICY_ACTION
			or target.predecessor is not None
			or target.event.owner_id != identity.pk
			or target.event.state not in {Event.State.PUBLISHED, Event.State.ACTIVE}
			or target.event.event_id not in binding.event_ids
		):
			raise RegistrationUnavailable("registration policy unavailable")
		try:
			return UUID(target.correlation_identity)
		except (AttributeError, TypeError, ValueError) as exc:
			raise RegistrationUnavailable("registration policy unavailable") from exc

	def authorise(self, *, identity: Identity, action: str, target, timestamp: datetime) -> str:
		correlation_id = self._qualify(
			identity=identity,
			action=action,
			target=target,
			timestamp=timestamp,
		)
		return f"{AUTHORITY_REFERENCE_PREFIX}:{correlation_id}"

	def determine_eligibility(
		self,
		*,
		identity: Identity,
		action: str,
		target,
		timestamp: datetime,
	) -> TrustedRegistrationEligibility:
		self._qualify(
			identity=identity,
			action=action,
			target=target,
			timestamp=timestamp,
		)
		return TrustedRegistrationEligibility(
			basis_type=EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION,
			basis_reference=f"{ELIGIBILITY_REFERENCE_PREFIX}:{target.event.event_id}",
			policy_reference=f"{POLICY_IDENTITY}:{POLICY_VERSION}",
			evaluated_at=timestamp,
		)


__all__ = [
	"POLICY_ACTION",
	"POLICY_ENVIRONMENT",
	"POLICY_IDENTITY",
	"POLICY_VERSION",
	"PreAlphaSelfRegistrationPolicy",
	"RegistrationAccountRefused",
	"RegistrationPolicyBinding",
	"RegistrationPolicyDenied",
	"RegistrationUnavailable",
	"TrustedRegistrationEligibility",
]