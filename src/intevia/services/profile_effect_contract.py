"""Physical placement within the Django `core` application is an implementation
locality only. `PROFILE_EFFECT` is a distinct cross-domain governance seam.
CORE/Identity does not own or interpret profile meaning. SERVICE owns exact
source qualification and authorised proposal creation. `PROFILE_EFFECT` owns
only neutral proposal and disposition lineage. Any downstream profile
meaning, presentation, or use remains with the separately authorised
receiving domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import unicodedata
from uuid import UUID


class _StrEnum(str, Enum):
    """A str-valued Enum whose str() is its value (base of the
    enums in this module).
    """
    def __str__(self) -> str:
        """Return the member's value."""
        return self.value


class ProposalAction(_StrEnum):
    """The proposal lineage actions: CREATE_PROPOSAL, VOID_PROPOSAL and
    SUPERSEDE_PROPOSAL.
    """
    CREATE_PROPOSAL = "CREATE_PROPOSAL"
    VOID_PROPOSAL = "VOID_PROPOSAL"
    SUPERSEDE_PROPOSAL = "SUPERSEDE_PROPOSAL"


class ProposalState(_StrEnum):
    """A proposal transition's resulting state: ACTIVE or VOIDED."""
    ACTIVE = "ACTIVE"
    VOIDED = "VOIDED"


class ProjectionAction(_StrEnum):
    """The projection disposition actions: AUTHORISE_PROJECTION,
    DECLINE_PROJECTION and WITHDRAW_PROJECTION.
    """
    AUTHORISE_PROJECTION = "AUTHORISE_PROJECTION"
    DECLINE_PROJECTION = "DECLINE_PROJECTION"
    WITHDRAW_PROJECTION = "WITHDRAW_PROJECTION"


class ProjectionState(_StrEnum):
    """A proposal's projection state: UNAUTHORISED, AUTHORISED, DECLINED
    or WITHDRAWN.
    """
    UNAUTHORISED = "UNAUTHORISED"
    AUTHORISED = "AUTHORISED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"


class SubjectRelation(_StrEnum):
    """How the subject relates to the source. v1 has one relation:
    IMMUTABLE_ACTIVITY_ASSIGNEE (the Activity's assignee).
    """
    IMMUTABLE_ACTIVITY_ASSIGNEE = "IMMUTABLE_ACTIVITY_ASSIGNEE"


class EffectType(_StrEnum):
    """The kind of profile effect proposed. v1 has one type:
    SERVICE_ACTIVITY_SUBMISSION_TRANSITION_RECORDED.
    """
    SERVICE_ACTIVITY_SUBMISSION_TRANSITION_RECORDED = (
        "SERVICE_ACTIVITY_SUBMISSION_TRANSITION_RECORDED"
    )


CONTRACT_VERSION = 1
PROPOSAL_COMMAND_SCHEMA = "intevia.s013.profile-effect.proposal-command.v1"
PROJECTION_COMMAND_SCHEMA = "intevia.s013.profile-effect.projection-command.v1"
PROPOSAL_LINEAGE_SCHEMA = "intevia.s013.profile-effect.proposal-lineage.v1"
PROJECTION_LINEAGE_SCHEMA = "intevia.s013.profile-effect.projection-lineage.v1"
PROPOSAL_AUTHORITY_TARGET_SCHEMA = (
    "intevia.s013.profile-effect.proposal-authority-target.v1"
)
PROJECTION_AUTHORITY_TARGET_SCHEMA = (
    "intevia.s013.profile-effect.projection-authority-target.v1"
)

PROPOSAL_COMMAND_DOMAIN = b"INTEVIA:S013:PROFILE_EFFECT:PROPOSAL_COMMAND:v1\x00"
PROPOSAL_LINEAGE_DOMAIN = b"INTEVIA:S013:PROFILE_EFFECT:PROPOSAL_LINEAGE:v1\x00"
PROJECTION_COMMAND_DOMAIN = b"INTEVIA:S013:PROFILE_EFFECT:PROJECTION_COMMAND:v1\x00"
PROJECTION_LINEAGE_DOMAIN = b"INTEVIA:S013:PROFILE_EFFECT:PROJECTION_LINEAGE:v1\x00"
PROPOSAL_AUTHORITY_TARGET_DOMAIN = (
    b"INTEVIA:S013:PROFILE_EFFECT:PROPOSAL_AUTHORITY_TARGET:v1\x00"
)
PROJECTION_AUTHORITY_TARGET_DOMAIN = (
    b"INTEVIA:S013:PROFILE_EFFECT:PROJECTION_AUTHORITY_TARGET:v1\x00"
)

NEUTRAL_MESSAGE = (
    "This record states only that the subject, as the Activity's immutable "
    "assignee, performed the exact recorded `SUBMIT_WORK` transition and "
    "explicitly nominated that occurrence for possible neutral profile "
    "projection. It does not record completion, acceptance, correctness, "
    "competence, contribution, recognition, value, productivity, benefit, or "
    "suitability."
)


@dataclass(frozen=True, slots=True)
class CreateServiceSubmissionProposalCommand:
    """Inputs for proposing one Service Activity submission transition.

    Carries the credential, actor access epoch, Activity UUID, request
    reference and key, and occurred_at. This frozen value object performs
    no domain-value validation or source qualification; those checks
    are the caller's responsibility.
    """
    credential: object
    actor_access_epoch: int
    activity_id: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ProfileEffectProposalCorrectionCommand:
    """Inputs for voiding or superseding a proposal lineage's head.

    Carries the credential, access epoch, lineage UUID, expected head
    transition primary key and lineage reference, request reference
    and key, and occurred_at. The chosen action is not a field here.
    This class performs no domain-value or current-head validation.
    """
    credential: object
    actor_access_epoch: int
    lineage_id: UUID
    expected_head_transition_pk: int
    expected_head_lineage_reference: str
    request_reference: str
    idempotency_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ProfileEffectProjectionDispositionCommand:
    """Inputs for authorising, declining or withdrawing a projection.

    Carries the credential, access epoch, lineage UUID, expected proposal
    transition primary key and lineage reference, expected disposition
    primary key and reference (each optional), request reference and
    key, and occurred_at. The chosen action is not a field here.
    This class validates neither values nor the optional-field pairing;
    it does not establish that the expected records are current.
    """
    credential: object
    actor_access_epoch: int
    lineage_id: UUID
    expected_proposal_transition_pk: int
    expected_proposal_lineage_reference: str
    expected_disposition_pk_or_null: int | None
    expected_disposition_lineage_reference_or_null: str | None
    request_reference: str
    idempotency_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ProfileEffectProposalCommandReceipt:
    """What a proposal command returns: the database alias, lineage,
    transition key, action and resulting state, its lineage reference,
    whether a surviving proposal remains, the request reference and key,
    the payload fingerprint, occurred_at, and whether it was a replay.
    """
    database_alias: str
    lineage_id: UUID
    proposal_transition_pk: int
    action: ProposalAction
    to_state: ProposalState
    proposal_lineage_reference: str
    has_current_survivor: bool
    request_reference: str
    idempotency_key: str
    payload_fingerprint: str
    occurred_at: datetime
    replayed: bool


@dataclass(frozen=True, slots=True)
class ProfileEffectProjectionCommandReceipt:
    """What a projection command returns: the database alias, lineage,
    proposal transition and disposition keys, action and resulting
    state, its lineage reference, the request reference and key, the
    payload fingerprint, occurred_at, and whether it was a replay.
    """
    database_alias: str
    lineage_id: UUID
    proposal_transition_pk: int
    projection_disposition_pk: int
    action: ProjectionAction
    to_state: ProjectionState
    projection_lineage_reference: str
    request_reference: str
    idempotency_key: str
    payload_fingerprint: str
    occurred_at: datetime
    replayed: bool


@dataclass(frozen=True, slots=True)
class SubjectProfileEffectProposalHistoryEntryDTO:
    """One proposal transition in a subject's read: action, actor
    identity UUID, occurred_at and lineage reference.
    """
    action: ProposalAction
    actor_identity_id: UUID
    occurred_at: datetime
    lineage_reference: str


@dataclass(frozen=True, slots=True)
class SubjectProfileEffectDispositionHistoryEntryDTO:
    """One projection disposition in a subject's read: action, actor
    identity UUID, occurred_at and lineage reference.
    """
    action: ProjectionAction
    actor_identity_id: UUID
    occurred_at: datetime
    lineage_reference: str


@dataclass(frozen=True, slots=True)
class SubjectProfileEffectReadDTO:
    """The subject's read of one profile-effect proposal lineage.

    Carries the source Activity and submission transition identifiers,
    subject and relation, effect type, fixed neutral message and proposal
    history. has_current_survivor states whether a proposal survives.

    The read service supplies disposition history only for that current
    surviving proposal, not for every proposal in the lineage. Without
    a survivor it supplies an empty disposition history and None for
    current_projection_state; otherwise it supplies the survivor's
    state: its last disposition's state, or UNAUTHORISED if none.
    This data object does not itself enforce subject-only access.
    """
    lineage_id: UUID
    source_activity_id: UUID
    source_transition_action: str
    source_transition_sequence: int
    source_transition_lineage_reference: str
    subject_identity_id: UUID
    subject_relation: SubjectRelation
    effect_type: EffectType
    neutral_message: str
    proposal_history: tuple[SubjectProfileEffectProposalHistoryEntryDTO, ...]
    has_current_survivor: bool
    current_projection_state: ProjectionState | None
    disposition_history: tuple[SubjectProfileEffectDispositionHistoryEntryDTO, ...]


def canonical_timestamp(value: datetime) -> str:
    """Return a timezone-aware datetime as a UTC string,
    YYYY-MM-DDTHH:MM:SS.ffffffZ.

    Accepts any datetime instance (subclasses included) with a usable
    UTC offset; anything else raises ValueError.
    """
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonicalise(value: object) -> object:
    """Prepare values for canonical JSON, with an unvalidated Enum path.

    Accept None, exact bool and int, NFC exact str, exact UUID as str,
    and exact aware datetime via canonical_timestamp. Recurse through
    exact list and tuple values (returning lists) and exact dict values;
    dict keys must be NFC exact str and remain unchanged.

    An Enum returns its value directly, without recursively checking or
    converting it. That value can therefore bypass the rules above and
    may still fail later JSON serialization.

    Exact floats, non-NFC strings, invalid dict keys and naive exact
    datetimes raise ValueError. Other unsupported types raise TypeError.
    Recursive conversion and timestamp errors propagate.
    """
    if value is None:
        return None
    if type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        raise ValueError("floats are outside the canonical profile")
    if type(value) is str:
        normalised = unicodedata.normalize("NFC", value)
        if normalised != value:
            raise ValueError("text must be NFC-normalised")
        return value
    if type(value) is UUID:
        return str(value)
    if type(value) is datetime:
        return canonical_timestamp(value)
    if type(value) is list:
        return [_canonicalise(item) for item in value]
    if type(value) is tuple:
        return [_canonicalise(item) for item in value]
    if type(value) is dict:
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("canonical object keys must be strings")
            if unicodedata.normalize("NFC", key) != key:
                raise ValueError("canonical field name is invalid")
            if key in result:
                raise ValueError("duplicate canonical field")
            result[key] = _canonicalise(item)
        return result
    if isinstance(value, Enum):
        return value.value
    raise TypeError("unsupported canonical value")


def canonical_json_bytes(values: dict[str, object]) -> bytes:
    """Serialize an exact dict to sorted, compact UTF-8 JSON bytes.

    Reject other top-level types with TypeError. Prepare keys and values
    with _canonicalise, then serialize without ASCII escaping and with
    nonfinite floats disallowed. Enum values returned by _canonicalise
    are not recursively validated against its canonical profile.

    Canonicalization, JSON serialization and UTF-8 encoding errors
    propagate. This does not validate a domain-specific payload schema.
    """
    if type(values) is not dict:
        raise TypeError("canonical value must be an object")
    return json.dumps(
        _canonicalise(values),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def bounded_reference(value: str, name: str, maximum: int) -> str:
    """Return a reference with surrounding whitespace stripped.

    value must be an exact str; once stripped it must be non-empty, at
    most maximum characters and NFC-normalised. Otherwise ValueError
    "<name> is invalid". Unlike the S015 canonical keys, surrounding
    whitespace is removed rather than refused.
    """
    if type(value) is not str:
        raise ValueError(f"{name} is invalid")
    canonical = value.strip()
    if (
        not canonical
        or len(canonical) > maximum
        or unicodedata.normalize("NFC", canonical) != canonical
    ):
        raise ValueError(f"{name} is invalid")
    return canonical


def proposal_authority_target_payload(*, database_alias: str, qualification_reference: str, subject_identity_id: UUID, subject_relation: SubjectRelation = SubjectRelation.IMMUTABLE_ACTIVITY_ASSIGNEE, effect_type: EffectType = EffectType.SERVICE_ACTIVITY_SUBMISSION_TRANSITION_RECORDED) -> dict[str, object]:
    """Build the authority target for creating a proposal.

    Records the contract version, database alias, effect type,
    qualification reference, schema, subject identity and relation.
    Builds only; nothing is checked or hashed here.
    """
    return {
        "contract_version": CONTRACT_VERSION,
        "database_alias": database_alias,
        "effect_type": effect_type.value,
        "qualification_reference": qualification_reference,
        "schema": PROPOSAL_AUTHORITY_TARGET_SCHEMA,
        "subject_identity_id": str(subject_identity_id),
        "subject_relation": subject_relation.value,
    }


def proposal_correction_target_payload(*, database_alias: str, lineage_id: UUID, subject_identity_id: UUID, current_proposal_transition_pk: int, current_proposal_lineage_reference: str, current_proposal_state: ProposalState, has_current_survivor: bool, action: ProposalAction) -> dict[str, object]:
    """Build the authority target for voiding or superseding a proposal.

    Records the action, contract version, the current head transition
    (key, lineage reference, state), whether a survivor remains, the
    database alias, lineage and subject. Uses the same schema name as
    the create target. Builds only.
    """
    return {
        "action": action.value,
        "contract_version": CONTRACT_VERSION,
        "current_proposal_lineage_reference": current_proposal_lineage_reference,
        "current_proposal_state": current_proposal_state.value,
        "current_proposal_transition_pk": current_proposal_transition_pk,
        "database_alias": database_alias,
        "has_current_survivor": has_current_survivor,
        "lineage_id": str(lineage_id),
        "schema": PROPOSAL_AUTHORITY_TARGET_SCHEMA,
        "subject_identity_id": str(subject_identity_id),
    }


def projection_authority_target_payload(*, database_alias: str, lineage_id: UUID, subject_identity_id: UUID, current_proposal_transition_pk: int, current_proposal_lineage_reference: str, current_disposition_pk_or_null: int | None, current_disposition_lineage_reference_or_null: str | None, current_projection_state: ProjectionState, action: ProjectionAction) -> dict[str, object]:
    """Build the authority target for a projection disposition.

    Records the action, contract version, the current disposition (key
    and reference, or None), the current projection state, the current
    proposal transition (key, reference), database alias, lineage and
    subject. Builds only.
    """
    return {
        "action": action.value,
        "contract_version": CONTRACT_VERSION,
        "current_disposition_lineage_reference_or_null": current_disposition_lineage_reference_or_null,
        "current_disposition_pk_or_null": current_disposition_pk_or_null,
        "current_projection_state": current_projection_state.value,
        "current_proposal_lineage_reference": current_proposal_lineage_reference,
        "current_proposal_transition_pk": current_proposal_transition_pk,
        "database_alias": database_alias,
        "lineage_id": str(lineage_id),
        "schema": PROJECTION_AUTHORITY_TARGET_SCHEMA,
        "subject_identity_id": str(subject_identity_id),
    }


def digest_hex(domain: bytes, payload: dict[str, object]) -> str:
    """Return the SHA-256 hex digest of domain followed by the canonical
    JSON of payload.
    """
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def proposal_authority_target_reference(payload: dict[str, object]) -> str:
    """Return "s013pt1:" plus the proposal target digest."""
    return f"s013pt1:{digest_hex(PROPOSAL_AUTHORITY_TARGET_DOMAIN, payload)}"


def projection_authority_target_reference(payload: dict[str, object]) -> str:
    """Return "s013xt1:" plus the projection target digest."""
    return f"s013xt1:{digest_hex(PROJECTION_AUTHORITY_TARGET_DOMAIN, payload)}"


def proposal_command_fingerprint(payload: dict[str, object]) -> str:
    """Return the proposal command payload's digest (hex, no prefix)."""
    return digest_hex(PROPOSAL_COMMAND_DOMAIN, payload)


def projection_command_fingerprint(payload: dict[str, object]) -> str:
    """Return the projection command payload's digest (hex, no prefix)."""
    return digest_hex(PROJECTION_COMMAND_DOMAIN, payload)


def proposal_lineage_reference(payload: dict[str, object]) -> str:
    """Return "s013pl1:" plus the proposal lineage digest."""
    return f"s013pl1:{digest_hex(PROPOSAL_LINEAGE_DOMAIN, payload)}"


def projection_lineage_reference(payload: dict[str, object]) -> str:
    """Return "s013xl1:" plus the projection lineage digest."""
    return f"s013xl1:{digest_hex(PROJECTION_LINEAGE_DOMAIN, payload)}"


__all__ = [
    "CONTRACT_VERSION",
    "CreateServiceSubmissionProposalCommand",
    "EffectType",
    "NEUTRAL_MESSAGE",
    "PROJECTION_AUTHORITY_TARGET_SCHEMA",
    "PROJECTION_COMMAND_SCHEMA",
    "PROPOSAL_AUTHORITY_TARGET_SCHEMA",
    "PROPOSAL_COMMAND_SCHEMA",
    "ProfileEffectProjectionCommandReceipt",
    "ProfileEffectProjectionDispositionCommand",
    "ProfileEffectProposalCommandReceipt",
    "ProfileEffectProposalCorrectionCommand",
    "ProjectionAction",
    "ProjectionState",
    "ProposalAction",
    "ProposalState",
    "SubjectProfileEffectDispositionHistoryEntryDTO",
    "SubjectProfileEffectProposalHistoryEntryDTO",
    "SubjectProfileEffectReadDTO",
    "SubjectRelation",
    "bounded_reference",
    "canonical_json_bytes",
    "canonical_timestamp",
    "projection_authority_target_payload",
    "projection_authority_target_reference",
    "projection_command_fingerprint",
    "projection_lineage_reference",
    "proposal_authority_target_payload",
    "proposal_authority_target_reference",
    "proposal_command_fingerprint",
    "proposal_correction_target_payload",
    "proposal_lineage_reference",
]