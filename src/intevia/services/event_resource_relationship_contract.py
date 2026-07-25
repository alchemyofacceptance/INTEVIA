"""EVENT-owned contracts for governed resource relationships."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import re
import unicodedata
from typing import Protocol
from uuid import UUID

from core.models import Identity


AUTHORITY_POLICY_REFERENCE = (
    "policy:EVENT-RESOURCE-RELATIONSHIP-AUTHORITY-PREALPHA-001:v1"
)
DISCLOSURE_POLICY_REFERENCE = (
    "policy:EVENT-RESOURCE-RELATIONSHIP-DISCLOSURE-PREALPHA-001:v1"
)
POLICY_ENVIRONMENT = "internal-pre-alpha"
SCHEMA_ID = "intevia.s011b.event-resource-relationship-determination"
SCHEMA_VERSION = 1
CANONICALIZATION = "RFC8785+INTEVIA-S011B-v1"
DOMAIN_SEPARATOR = b"INTEVIA:S011B:EVENT-RESOURCE-RELATIONSHIP:v1\n"

_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}\Z")
_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]{0,254}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class EventRelationshipAction(StrEnum):
    CREATE = "CREATE"
    SUPERSEDE_VERSION = "SUPERSEDE_VERSION"
    AMEND_PURPOSE = "AMEND_PURPOSE"
    RETIRE = "RETIRE"
    VOID = "VOID"


class EventAuthorityResult(StrEnum):
    QUALIFIED = "QUALIFIED"
    REFUSED = "REFUSED"
    HOLD = "HOLD"


class RelationshipDisclosureResult(StrEnum):
    VISIBLE = "VISIBLE"
    HIDDEN = "HIDDEN"
    HOLD = "HOLD"


class ExistenceDisclosureResult(StrEnum):
    EXISTENCE_VISIBLE = "EXISTENCE_VISIBLE"
    HIDDEN = "HIDDEN"
    HOLD = "HOLD"


class RelationshipPurpose(StrEnum):
    PREPARATION = "PREPARATION"
    DURING_EVENT = "DURING_EVENT"
    FOLLOW_UP = "FOLLOW_UP"
    REFERENCE = "REFERENCE"

    @property
    def display(self) -> str:
        return {
            RelationshipPurpose.PREPARATION: "Preparation",
            RelationshipPurpose.DURING_EVENT: "During the Event",
            RelationshipPurpose.FOLLOW_UP: "Follow-up",
            RelationshipPurpose.REFERENCE: "Reference",
        }[self]


class RelationshipState(StrEnum):
    CURRENT = "CURRENT"
    RETIRED = "RETIRED"
    SUPERSEDED = "SUPERSEDED"
    VOIDED = "VOIDED"


class VoidReason(StrEnum):
    WRONG_EVENT = "WRONG_EVENT"
    WRONG_RESOURCE = "WRONG_RESOURCE"
    WRONG_VERSION = "WRONG_VERSION"
    WRONG_PURPOSE = "WRONG_PURPOSE"
    DUPLICATE_ASSERTION = "DUPLICATE_ASSERTION"
    OTHER_GOVERNED_CORRECTION = "OTHER_GOVERNED_CORRECTION"


class BindingDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class BindingLookupStatus(StrEnum):
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    UNAVAILABLE = "UNAVAILABLE"


def _canonical_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_uuid(value: UUID | str, name: str) -> str:
    try:
        parsed = value if isinstance(value, UUID) else UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc
    canonical = str(parsed)
    if isinstance(value, str) and value != canonical:
        raise ValueError(f"{name} must be a lowercase canonical UUID")
    return canonical


def _bounded(value: str, name: str, pattern: re.Pattern[str]) -> str:
    if (
        type(value) is not str
        or unicodedata.normalize("NFC", value) != value
        or pattern.fullmatch(value) is None
    ):
        raise ValueError(f"{name} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class EventRelationshipAuthorityTarget:
    actor_identity_id: str
    actor_access_epoch: int
    action: EventRelationshipAction
    authority_scope: str
    event_id: str
    event_state: str
    relationship_id: str | None
    current_assertion_id: int | None
    resource_id: str
    version_number: int
    current_purpose: RelationshipPurpose | None
    proposed_purpose: RelationshipPurpose | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        _canonical_uuid(self.actor_identity_id, "actor_identity_id")
        if type(self.actor_access_epoch) is not int or self.actor_access_epoch < 0:
            raise ValueError("actor_access_epoch must be non-negative")
        if not isinstance(self.action, EventRelationshipAction):
            raise ValueError("action is invalid")
        _bounded(self.authority_scope, "authority_scope", _OPAQUE)
        _bounded(self.event_id, "event_id", _REFERENCE)
        _bounded(self.resource_id, "resource_id", _REFERENCE)
        if self.relationship_id is not None:
            _bounded(self.relationship_id, "relationship_id", _REFERENCE)
        if self.current_assertion_id is not None and (
            type(self.current_assertion_id) is not int or self.current_assertion_id < 1
        ):
            raise ValueError("current_assertion_id is invalid")
        if type(self.version_number) is not int or self.version_number < 1:
            raise ValueError("version_number is invalid")
        if self.current_purpose is not None and not isinstance(
            self.current_purpose, RelationshipPurpose
        ):
            raise ValueError("current_purpose is invalid")
        if self.proposed_purpose is not None and not isinstance(
            self.proposed_purpose, RelationshipPurpose
        ):
            raise ValueError("proposed_purpose is invalid")
        _canonical_timestamp(self.occurred_at)


@dataclass(frozen=True, slots=True)
class EventAuthorityBindingSnapshot:
    binding_reference: str
    binding_version: int
    subject_identity_id: str
    action: EventRelationshipAction
    authority_scope: str
    event_id: str
    enabled: bool
    effective_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    superseding_binding_reference: str | None
    provider_snapshot_reference: str
    decision: BindingDecision


@dataclass(frozen=True, slots=True)
class RelationshipDisclosureBindingSnapshot:
    binding_reference: str
    binding_version: int
    subject_identity_id: str
    event_id: str
    relationship_id: str
    enabled: bool
    effective_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    superseding_binding_reference: str | None
    provider_snapshot_reference: str
    decision: BindingDecision


@dataclass(frozen=True, slots=True)
class BindingLookup:
    status: BindingLookupStatus
    snapshot: EventAuthorityBindingSnapshot | RelationshipDisclosureBindingSnapshot | None = None


@dataclass(frozen=True, slots=True)
class EventRelationshipAuthorityEnvelope:
    result: EventAuthorityResult
    target: EventRelationshipAuthorityTarget
    policy_reference: str
    binding_reference: str | None
    provider_snapshot_reference: str | None
    evaluated_at: datetime
    canonical_payload: bytes
    determination_reference: str


@dataclass(frozen=True, slots=True)
class RelationshipDisclosureEnvelope:
    result: RelationshipDisclosureResult
    existence_result: ExistenceDisclosureResult
    viewer_identity_id: str
    viewer_access_epoch: int
    event_id: str
    relationship_id: str
    assertion_id: int
    state: RelationshipState
    purpose: RelationshipPurpose
    policy_reference: str
    binding_reference: str | None
    provider_snapshot_reference: str | None
    evaluated_at: datetime
    canonical_payload: bytes
    determination_reference: str


class EventAuthorityBindingProvider(Protocol):
    def lookup_authority(
        self,
        *,
        target: EventRelationshipAuthorityTarget,
        evaluated_at: datetime,
    ) -> BindingLookup: ...


class RelationshipDisclosureBindingProvider(Protocol):
    def lookup_disclosure(
        self,
        *,
        viewer_identity_id: str,
        event_id: str,
        relationship_id: str,
        evaluated_at: datetime,
    ) -> BindingLookup: ...


class EventRelationshipAuthority(Protocol):
    def determine_authority(
        self,
        *,
        identity: Identity,
        target: EventRelationshipAuthorityTarget,
        evaluated_at: datetime,
    ) -> EventRelationshipAuthorityEnvelope: ...


class EventRelationshipDisclosure(Protocol):
    def determine_disclosure(
        self,
        *,
        identity: Identity,
        event_id: str,
        relationship_id: str,
        assertion_id: int,
        state: RelationshipState,
        purpose: RelationshipPurpose,
        evaluated_at: datetime,
    ) -> RelationshipDisclosureEnvelope: ...


def canonical_event_payload_bytes(payload: dict) -> bytes:
    if type(payload) is not dict or not payload:
        raise ValueError("payload must be a non-empty object")
    for name, value in payload.items():
        if type(name) is not str or unicodedata.normalize("NFC", name) != name:
            raise ValueError("payload field name is invalid")
        if type(value) is float:
            raise ValueError("floats are outside the canonical profile")
        if type(value) is str and unicodedata.normalize("NFC", value) != value:
            raise ValueError(f"{name} is not NFC text")
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def determination_reference_for(canonical_payload: bytes) -> str:
    if type(canonical_payload) is not bytes:
        raise TypeError("canonical_payload must be bytes")
    digest = hashlib.sha256(DOMAIN_SEPARATOR + canonical_payload).hexdigest()
    return f"event-resource-determination:sha256:{digest}"


def authority_payload(
    target: EventRelationshipAuthorityTarget,
    *,
    result: EventAuthorityResult,
    binding_reference: str | None,
    provider_snapshot_reference: str | None,
    evaluated_at: datetime,
) -> bytes:
    values = asdict(target)
    values.update(
        {
            "action": target.action.value,
            "current_purpose": target.current_purpose.value if target.current_purpose else None,
            "proposed_purpose": target.proposed_purpose.value if target.proposed_purpose else None,
            "occurred_at": _canonical_timestamp(target.occurred_at),
            "actor_access_epoch": str(target.actor_access_epoch),
            "current_assertion_id": (
                str(target.current_assertion_id)
                if target.current_assertion_id is not None
                else None
            ),
            "version_number": str(target.version_number),
            "binding_reference": binding_reference,
            "canonicalization": CANONICALIZATION,
            "environment": POLICY_ENVIRONMENT,
            "evaluated_at": _canonical_timestamp(evaluated_at),
            "policy_reference": AUTHORITY_POLICY_REFERENCE,
            "provider_snapshot_reference": provider_snapshot_reference,
            "result": result.value,
            "schema_id": SCHEMA_ID,
            "schema_version": SCHEMA_VERSION,
        }
    )
    return canonical_event_payload_bytes(values)


__all__ = [
    "AUTHORITY_POLICY_REFERENCE",
    "BindingDecision",
    "BindingLookup",
    "BindingLookupStatus",
    "CANONICALIZATION",
    "DISCLOSURE_POLICY_REFERENCE",
    "EventAuthorityBindingProvider",
    "EventAuthorityBindingSnapshot",
    "EventAuthorityResult",
    "EventRelationshipAction",
    "EventRelationshipAuthority",
    "EventRelationshipAuthorityEnvelope",
    "EventRelationshipAuthorityTarget",
    "EventRelationshipDisclosure",
    "ExistenceDisclosureResult",
    "POLICY_ENVIRONMENT",
    "RelationshipDisclosureBindingProvider",
    "RelationshipDisclosureBindingSnapshot",
    "RelationshipDisclosureEnvelope",
    "RelationshipDisclosureResult",
    "RelationshipPurpose",
    "RelationshipState",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "VoidReason",
    "authority_payload",
    "canonical_event_payload_bytes",
    "determination_reference_for",
]