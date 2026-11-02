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
    """The five commands on an Event-resource relationship: CREATE,
    SUPERSEDE_VERSION, AMEND_PURPOSE, RETIRE and VOID."""
    CREATE = "CREATE"
    SUPERSEDE_VERSION = "SUPERSEDE_VERSION"
    AMEND_PURPOSE = "AMEND_PURPOSE"
    RETIRE = "RETIRE"
    VOID = "VOID"


class EventAuthorityResult(StrEnum):
    """An authority determination: QUALIFIED, REFUSED or HOLD (not
    decided, for example when no usable binding is available)."""
    QUALIFIED = "QUALIFIED"
    REFUSED = "REFUSED"
    HOLD = "HOLD"


class RelationshipDisclosureResult(StrEnum):
    """Whether a relationship's details may be shown to a viewer:
    VISIBLE, HIDDEN or HOLD."""
    VISIBLE = "VISIBLE"
    HIDDEN = "HIDDEN"
    HOLD = "HOLD"


class ExistenceDisclosureResult(StrEnum):
    """Whether a relationship's existence may be shown to a viewer:
    EXISTENCE_VISIBLE, HIDDEN or HOLD."""
    EXISTENCE_VISIBLE = "EXISTENCE_VISIBLE"
    HIDDEN = "HIDDEN"
    HOLD = "HOLD"


class RelationshipPurpose(StrEnum):
    """Why a resource relates to an Event: PREPARATION, DURING_EVENT,
    FOLLOW_UP or REFERENCE."""
    PREPARATION = "PREPARATION"
    DURING_EVENT = "DURING_EVENT"
    FOLLOW_UP = "FOLLOW_UP"
    REFERENCE = "REFERENCE"

    @property
    def display(self) -> str:
        """The purpose's display label, for example "During the Event"."""
        return {
            RelationshipPurpose.PREPARATION: "Preparation",
            RelationshipPurpose.DURING_EVENT: "During the Event",
            RelationshipPurpose.FOLLOW_UP: "Follow-up",
            RelationshipPurpose.REFERENCE: "Reference",
        }[self]


class RelationshipState(StrEnum):
    """A relationship assertion's state: CURRENT, RETIRED, SUPERSEDED or
    VOIDED."""
    CURRENT = "CURRENT"
    RETIRED = "RETIRED"
    SUPERSEDED = "SUPERSEDED"
    VOIDED = "VOIDED"


class VoidReason(StrEnum):
    """Why an assertion was voided: wrong Event, resource, version or
    purpose, a duplicate assertion, or another governed correction."""
    WRONG_EVENT = "WRONG_EVENT"
    WRONG_RESOURCE = "WRONG_RESOURCE"
    WRONG_VERSION = "WRONG_VERSION"
    WRONG_PURPOSE = "WRONG_PURPOSE"
    DUPLICATE_ASSERTION = "DUPLICATE_ASSERTION"
    OTHER_GOVERNED_CORRECTION = "OTHER_GOVERNED_CORRECTION"


class BindingDecision(StrEnum):
    """A binding snapshot's decision: ALLOW or DENY."""
    ALLOW = "ALLOW"
    DENY = "DENY"


class BindingLookupStatus(StrEnum):
    """A binding lookup's outcome: MATCH (one snapshot found), NO_MATCH
    (none, and the provider is complete) or UNAVAILABLE."""
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    UNAVAILABLE = "UNAVAILABLE"


def _canonical_timestamp(value: datetime) -> str:
    """Format a timezone-aware datetime as UTC text with microseconds and
    "Z"; a non-datetime or naive value raises ValueError. Subclasses of
    datetime are accepted."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_uuid(value: UUID | str, name: str) -> str:
    """Return canonical lowercase UUID text.

    Use a UUID instance directly; otherwise parse with UUID(value).
    Convert TypeError, ValueError or AttributeError from that parse to
    ValueError naming the field. A str input must already equal the
    canonical text, else ValueError. No separate str-only type gate is
    applied to other inputs. The supplied object is not modified.
    """
    try:
        parsed = value if isinstance(value, UUID) else UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc
    canonical = str(parsed)
    if isinstance(value, str) and value != canonical:
        raise ValueError(f"{name} must be a lowercase canonical UUID")
    return canonical


def _bounded(value: str, name: str, pattern: re.Pattern[str]) -> str:
    """Return value if it is an exact str, NFC, and fully matches pattern;
    otherwise ValueError naming the field. Nothing is stripped."""
    if (
        type(value) is not str
        or unicodedata.normalize("NFC", value) != value
        or pattern.fullmatch(value) is None
    ):
        raise ValueError(f"{name} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class EventRelationshipAuthorityTarget:
    """Carry the actor, command and proposed Event-resource context.

    Hold actor identity and epoch, action and scope, Event id and state,
    optional relationship and current assertion ids, resource/version,
    current and proposed purposes, and occurrence time. Construction
    checks selected field shapes through __post_init__; it does not
    establish stored facts, authority or action-specific combinations.
    event_state is unchecked, and None assertion/relationship ids are
    not restricted here to CREATE.
    """
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
        """Check target field shapes in order, without normalising fields.

        Check actor UUID form, then an exact non-negative int epoch, then
        an EventRelationshipAction. Check scope against the ASCII opaque
        pattern (1-128 characters); check Event, resource and any
        relationship id against the reference pattern (1-255 characters).
        Each starts alphanumeric; later characters allow . _ ~ - and,
        for references only, colon. These fields must be exact NFC str.

        Next require any current assertion id and the version to be exact
        positive ints; check current then proposed purpose as None or a
        RelationshipPurpose; finally require an aware datetime occurrence.
        Explicit check failures raise ValueError; unhandled parser or
        time-zone/conversion errors propagate. Success returns None.

        The UUID and timestamp helpers' formatted results are discarded.
        A UUID actor object is retained, and can fail later JSON encoding.
        event_state, row existence, authority, chronology and action-specific
        combinations of ids and purposes are not checked here.
        """
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
    """One immutable authority binding as a provider holds it: reference
    and version, subject identity, action, scope, Event, enabled flag,
    effective and expiry times, revocation, supersession, the provider's
    snapshot reference and the ALLOW or DENY decision. Nothing is
    validated here; the policy checks it is current.
    """
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
    """One immutable disclosure binding: reference and version, subject
    identity, Event, relationship, enabled flag, effective and expiry
    times, revocation, supersession, the provider's snapshot reference
    and the decision. Nothing is validated here.
    """
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
    """A provider's answer: a status and, for MATCH, the snapshot."""
    status: BindingLookupStatus
    snapshot: EventAuthorityBindingSnapshot | RelationshipDisclosureBindingSnapshot | None = None


@dataclass(frozen=True, slots=True)
class EventRelationshipAuthorityEnvelope:
    """An authority determination as recorded: the result, the target,
    the policy, binding and provider snapshot references, the evaluation
    time, the canonical payload and its determination reference. The
    class validates nothing and can be built directly.
    """
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
    """A disclosure determination as recorded: the two results, the
    viewer and epoch, the Event, relationship and assertion, its state
    and purpose, the references, the evaluation time, the canonical
    payload and its determination reference. Validates nothing.
    """
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
    """The interface of a source of authority binding snapshots."""
    def lookup_authority(
        self,
        *,
        target: EventRelationshipAuthorityTarget,
        evaluated_at: datetime,
    ) -> BindingLookup:
        """Return the BindingLookup for a target at evaluated_at."""
        ...


class RelationshipDisclosureBindingProvider(Protocol):
    """The interface of a source of disclosure binding snapshots."""
    def lookup_disclosure(
        self,
        *,
        viewer_identity_id: str,
        event_id: str,
        relationship_id: str,
        evaluated_at: datetime,
    ) -> BindingLookup:
        """Return the BindingLookup for a viewer, Event and relationship at
        evaluated_at."""
        ...


class EventRelationshipAuthority(Protocol):
    """The interface of an authority that decides relationship commands."""
    def determine_authority(
        self,
        *,
        identity: Identity,
        target: EventRelationshipAuthorityTarget,
        evaluated_at: datetime,
    ) -> EventRelationshipAuthorityEnvelope:
        """Return the authority envelope for an Identity and a target."""
        ...


class EventRelationshipDisclosure(Protocol):
    """The interface of a policy that decides relationship disclosure."""
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
    ) -> RelationshipDisclosureEnvelope:
        """Return the disclosure envelope for a viewer and one assertion."""
        ...


def canonical_event_payload_bytes(payload: dict) -> bytes:
    """Encode a nonempty exact dict using the local JSON byte profile.

    Require exact NFC str top-level keys; reject exact float top-level
    values and non-NFC exact str values, raising ValueError. These
    checks are shallow: they do not require flat data, validate nested
    values, or apply the same tests to value subclasses.

    Use json.dumps with sorted keys, compact separators, ensure_ascii
    False and allow_nan False, then UTF-8 encode. Unsupported values
    raise TypeError; non-finite numbers or circular containers can
    raise ValueError, and invalid Unicode can raise UnicodeEncodeError.
    Other encoder errors propagate. No schema or authority is checked;
    this implementation is not a general RFC 8785 canonicalizer.
    """
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
    """Return "event-resource-determination:sha256:" plus the SHA-256 of
    the domain separator followed by the payload bytes; TypeError unless
    the payload is exact bytes. An unkeyed digest."""
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
    """Build authority-determination bytes from the supplied target.

    Start with asdict(target). Replace action and nonempty purposes by
    their values; format occurrence and evaluation as UTC microsecond
    timestamps. Stringify epoch, version and any current assertion id.
    Add the supplied binding/provider references, result.value and the
    fixed schema, policy, environment and canonicalization labels;
    then use canonical_event_payload_bytes.

    Do not repeat target construction checks or validate the references.
    event_state and actor_identity_id remain as supplied; a UUID actor
    object fails JSON encoding with TypeError. Non-dataclass input can
    fail asdict with TypeError; missing value attributes can raise
    AttributeError. Timestamp, copying, JSON and UTF-8 errors propagate.
    The bytes record a claimed determination, not its provenance or
    proof that the policy qualified it.
    """
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