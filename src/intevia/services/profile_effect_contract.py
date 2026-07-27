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
    def __str__(self) -> str:
        return self.value


class ProposalAction(_StrEnum):
    CREATE_PROPOSAL = "CREATE_PROPOSAL"
    VOID_PROPOSAL = "VOID_PROPOSAL"
    SUPERSEDE_PROPOSAL = "SUPERSEDE_PROPOSAL"


class ProposalState(_StrEnum):
    ACTIVE = "ACTIVE"
    VOIDED = "VOIDED"


class ProjectionAction(_StrEnum):
    AUTHORISE_PROJECTION = "AUTHORISE_PROJECTION"
    DECLINE_PROJECTION = "DECLINE_PROJECTION"
    WITHDRAW_PROJECTION = "WITHDRAW_PROJECTION"


class ProjectionState(_StrEnum):
    UNAUTHORISED = "UNAUTHORISED"
    AUTHORISED = "AUTHORISED"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"


class SubjectRelation(_StrEnum):
    IMMUTABLE_ACTIVITY_ASSIGNEE = "IMMUTABLE_ACTIVITY_ASSIGNEE"


class EffectType(_StrEnum):
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
    credential: object
    actor_access_epoch: int
    activity_id: UUID
    request_reference: str
    idempotency_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ProfileEffectProposalCorrectionCommand:
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
    action: ProposalAction
    actor_identity_id: UUID
    occurred_at: datetime
    lineage_reference: str


@dataclass(frozen=True, slots=True)
class SubjectProfileEffectDispositionHistoryEntryDTO:
    action: ProjectionAction
    actor_identity_id: UUID
    occurred_at: datetime
    lineage_reference: str


@dataclass(frozen=True, slots=True)
class SubjectProfileEffectReadDTO:
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
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonicalise(value: object) -> object:
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
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


def proposal_authority_target_reference(payload: dict[str, object]) -> str:
    return f"s013pt1:{digest_hex(PROPOSAL_AUTHORITY_TARGET_DOMAIN, payload)}"


def projection_authority_target_reference(payload: dict[str, object]) -> str:
    return f"s013xt1:{digest_hex(PROJECTION_AUTHORITY_TARGET_DOMAIN, payload)}"


def proposal_command_fingerprint(payload: dict[str, object]) -> str:
    return digest_hex(PROPOSAL_COMMAND_DOMAIN, payload)


def projection_command_fingerprint(payload: dict[str, object]) -> str:
    return digest_hex(PROJECTION_COMMAND_DOMAIN, payload)


def proposal_lineage_reference(payload: dict[str, object]) -> str:
    return f"s013pl1:{digest_hex(PROPOSAL_LINEAGE_DOMAIN, payload)}"


def projection_lineage_reference(payload: dict[str, object]) -> str:
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