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
	"""A self-registration refused by the pre-alpha policy (a
	PermissionError). Raised only through its two subclasses."""
	pass


class RegistrationAccountRefused(RegistrationPolicyDenied):
	"""Pre-alpha account refusal: staff or superuser credential, inactive
	or unlisted Identity, or a target that is not that Identity's own
	SELF registration. Credential activity is checked separately by
	ContributionAuthority, not by this policy exception's raise site.
	"""
	pass


class RegistrationUnavailable(RegistrationPolicyDenied):
	"""The policy does not apply now or to this Event or request."""
	pass


@dataclass(frozen=True, slots=True)
class RegistrationPolicyBinding:
	"""The operator's binding of the pre-alpha policy, read from the
	environment: its identity, version and environment names, whether it
	is enabled, revoked or superseded, its effective and expiry times,
	and the listed identity and event ids. Nothing is checked here; the
	policy checks the binding on each request.
	"""
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
		"""Parse an ISO 8601 time ("Z" accepted); return None for an empty,
		unparseable or naive value instead of raising."""
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
		"""Split comma-separated text into a set of stripped, non-empty
		items; empty for None or blank text."""
		return frozenset(item.strip() for item in (value or "").split(",") if item.strip())

	@classmethod
	def from_environment(cls) -> RegistrationPolicyBinding:
		"""Build the binding from the INTEVIA_S010_POLICY_* variables.

		ENABLED and REVOKED are true only for "true" (any case); a missing
		or bad time becomes None, and an empty SUPERSEDED_BY None. Read each
		time it is called.
		"""
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
	"""The eligibility receipt the policy returns: basis type and
	reference, policy reference and evaluation time."""
	basis_type: str
	basis_reference: str
	policy_reference: str
	evaluated_at: datetime


class PreAlphaSelfRegistrationPolicy:
	"""Evaluate the Human-qualified policy; callers only adapt its result."""

	def __init__(self, binding: RegistrationPolicyBinding | None = None) -> None:
		"""Keep the given binding, or read one from the environment."""
		self.binding = binding or RegistrationPolicyBinding.from_environment()

	def _qualify(self, *, identity: Identity, action: str, target, timestamp: datetime) -> UUID:
		"""Qualify a request under REG-AUTH-PREALPHA-001 v1 only.

		Require the matching internal-pre-alpha binding, enabled and neither
		revoked nor superseded, with effective_at <= timestamp < expires_at.
		A failed binding/time condition raises RegistrationUnavailable.

		Require a nonstaff, nonsuperuser credential, an ACTIVE allow-listed
		Identity, that Identity as participant and SELF origin; otherwise
		raise RegistrationAccountRefused. Require register_self, no
		predecessor, an owned PUBLISHED or ACTIVE allow-listed Event;
		otherwise raise RegistrationUnavailable.

		Parse correlation_identity with UUID and return the parsed value.
		AttributeError, TypeError or ValueError in that final parse becomes
		RegistrationUnavailable. Other attribute/comparison errors propagate,
		including incompatible naive/aware times when comparison is reached.
		No independent timestamp-awareness or credential-activity check is
		made here. ContributionAuthority supplies its separate actor checks.
		This method writes nothing and does not define general Event access.
		"""
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
		"""Qualify the request; return "reg-auth-prealpha-001:v1:" plus its
		correlation id as the authority reference. Refusals propagate."""
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
		"""Qualify the request; return an EVENT_CONFIGURATION receipt.

		The basis reference names the Event's id, the policy reference
		"REG-AUTH-PREALPHA-001:v1", and the evaluation time is the given
		timestamp. The same checks as authorise run again; refusals
		propagate.
		"""
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