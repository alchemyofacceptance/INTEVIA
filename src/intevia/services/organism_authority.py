from __future__ import annotations

import hashlib
import inspect
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol
from uuid import UUID

from django.db import connections

from core.organism_contract import OrganismAction, qualification_planes


_TARGET_DOMAIN = b"INTEVIA:S015:ORGANISM_AUTHORITY_TARGET:v1\x00"
_DECISION_DOMAIN = b"INTEVIA:S015:ORGANISM_AUTHORITY_DECISION:v1\x00"


class S015AuthorityError(Exception):
    pass


class S015AuthorityUnavailable(S015AuthorityError):
    pass


class S015AuthorityMalformed(S015AuthorityError):
    pass


class S015AuthorityDenied(S015AuthorityError):
    pass


class S015AuthorityRefusalCode(str, Enum):
    DENIED = "DENIED"


@dataclass(frozen=True, slots=True)
class S015PreFoundingAuthorityRequest:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class S015PostFoundingAuthorityRequest:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    active_membership_head_reference: str
    contextual_role_head_references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class S015PreFoundingAuthorityResponse:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    authority_reference: str


@dataclass(frozen=True, slots=True)
class S015PostFoundingAuthorityResponse:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    active_membership_head_reference: str
    contextual_role_head_references: tuple[str, ...]
    authority_reference: str


@dataclass(frozen=True, slots=True)
class S015PreFoundingAuthorityRefusal:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    refusal_code: S015AuthorityRefusalCode


@dataclass(frozen=True, slots=True)
class S015PostFoundingAuthorityRefusal:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    active_membership_head_reference: str
    contextual_role_head_references: tuple[str, ...]
    refusal_code: S015AuthorityRefusalCode


S015AuthorityRequest = S015PreFoundingAuthorityRequest | S015PostFoundingAuthorityRequest
S015AuthorityResult = (
    S015PreFoundingAuthorityResponse
    | S015PostFoundingAuthorityResponse
    | S015PreFoundingAuthorityRefusal
    | S015PostFoundingAuthorityRefusal
)


class S015AuthorityProvider(Protocol):
    def evaluate_organism_authority(
        self, request: S015AuthorityRequest
    ) -> S015AuthorityResult: ...


@dataclass(frozen=True, slots=True)
class QualifiedS015AuthorityDecision:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: OrganismAction
    command_subtype: str
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    active_membership_head_reference: str | None
    contextual_role_head_references: tuple[str, ...]
    authority_reference: str
    authority_decision_reference: str


def canonical_timestamp(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    utc_value = value.astimezone(timezone.utc)
    return (
        f"{utc_value.year:04d}-{utc_value.month:02d}-{utc_value.day:02d}"
        f"T{utc_value.hour:02d}:{utc_value.minute:02d}:{utc_value.second:02d}"
        f".{utc_value.microsecond:06d}Z"
    )


def _canonicalise(value: object) -> object:
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is str:
        if unicodedata.normalize("NFC", value) != value:
            raise ValueError("strings must be NFC-normalized")
        return value
    if type(value) is UUID:
        return str(value)
    if type(value) is datetime:
        return canonical_timestamp(value)
    if isinstance(value, Enum):
        return _canonicalise(value.value)
    if type(value) in {list, tuple}:
        return [_canonicalise(item) for item in value]
    if type(value) is dict:
        result: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str or unicodedata.normalize("NFC", key) != key:
                raise ValueError("object keys must be NFC strings")
            result[key] = _canonicalise(item)
        return result
    raise TypeError("unsupported canonical primitive")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _canonicalise(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def authority_target_fingerprint(target: dict[str, object]) -> str:
    return hashlib.sha256(_TARGET_DOMAIN + canonical_json_bytes(target)).hexdigest()


def _canonical_reference(value: object, name: str, maximum: int) -> str:
    if type(value) is not str:
        raise S015AuthorityMalformed(f"{name} must be a string")
    canonical = unicodedata.normalize("NFC", value).strip()
    if canonical != value or not canonical or len(canonical) > maximum:
        raise S015AuthorityMalformed(f"{name} is not canonical")
    return canonical


def _validate_request(request: S015AuthorityRequest) -> None:
    if type(request) not in {
        S015PreFoundingAuthorityRequest,
        S015PostFoundingAuthorityRequest,
    }:
        raise S015AuthorityMalformed("authority request type is invalid")
    if type(request.database_alias) is not str or not request.database_alias:
        raise S015AuthorityMalformed("database_alias is invalid")
    if type(request.actor_pk) is not int or request.actor_pk < 1:
        raise S015AuthorityMalformed("actor_pk is invalid")
    if type(request.actor_identity_id) is not UUID:
        raise S015AuthorityMalformed("actor_identity_id is invalid")
    if type(request.actor_access_epoch) is not int or request.actor_access_epoch < 0:
        raise S015AuthorityMalformed("actor_access_epoch is invalid")
    if type(request.action) is not OrganismAction:
        raise S015AuthorityMalformed("action is invalid")
    qualification_planes(request.command_subtype)
    if not request.command_subtype.startswith(request.action.value):
        raise S015AuthorityMalformed("command subtype does not match action")
    if re.fullmatch(r"[0-9a-f]{64}", request.target_fingerprint or "") is None:
        raise S015AuthorityMalformed("target_fingerprint is invalid")
    _canonical_reference(request.request_reference, "request_reference", 128)
    _canonical_reference(request.idempotency_key, "idempotency_key", 120)
    canonical_timestamp(request.evaluated_at)
    if type(request) is S015PostFoundingAuthorityRequest:
        _canonical_reference(
            request.active_membership_head_reference,
            "active_membership_head_reference",
            255,
        )
        if type(request.contextual_role_head_references) is not tuple:
            raise S015AuthorityMalformed("contextual role heads must be a tuple")
        for reference in request.contextual_role_head_references:
            _canonical_reference(reference, "contextual_role_head_reference", 255)


def _response_types(request: S015AuthorityRequest) -> tuple[type[object], type[object]]:
    if type(request) is S015PreFoundingAuthorityRequest:
        return S015PreFoundingAuthorityResponse, S015PreFoundingAuthorityRefusal
    return S015PostFoundingAuthorityResponse, S015PostFoundingAuthorityRefusal


class S015Authority:
    def __init__(self, *, provider: S015AuthorityProvider, database_alias: str = "default"):
        method = getattr(provider, "evaluate_organism_authority", None)
        if provider is None or not callable(method):
            raise TypeError("provider must implement evaluate_organism_authority")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.provider = provider
        self.database_alias = database_alias

    def qualify(self, request: S015AuthorityRequest) -> QualifiedS015AuthorityDecision:
        try:
            _validate_request(request)
        except (TypeError, ValueError, UnicodeError) as exc:
            if isinstance(exc, S015AuthorityMalformed):
                raise
            raise S015AuthorityMalformed("authority request is malformed") from exc
        if request.database_alias != self.database_alias:
            raise S015AuthorityMalformed("database alias mismatch")
        if not connections[self.database_alias].in_atomic_block:
            raise S015AuthorityMalformed("outer atomic transaction required")
        try:
            result = self.provider.evaluate_organism_authority(request)
        except Exception as exc:
            raise S015AuthorityUnavailable("organism authority unavailable") from exc
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise S015AuthorityMalformed("authority response must be synchronous")
        response_type, refusal_type = _response_types(request)
        if type(result) not in {response_type, refusal_type}:
            raise S015AuthorityMalformed("authority response type is invalid")
        echoed = tuple(asdict(request))
        if any(getattr(result, field) != getattr(request, field) for field in echoed):
            raise S015AuthorityMalformed("authority response echo mismatch")
        if type(result) is refusal_type:
            if type(result.refusal_code) is not S015AuthorityRefusalCode:
                raise S015AuthorityMalformed("refusal code is invalid")
            raise S015AuthorityDenied("organism authority denied")
        authority_reference = _canonical_reference(
            result.authority_reference, "authority_reference", 255
        )
        decision_payload = asdict(result)
        decision_reference = "s015d1:" + hashlib.sha256(
            _DECISION_DOMAIN + canonical_json_bytes(decision_payload)
        ).hexdigest()
        post_founding = type(request) is S015PostFoundingAuthorityRequest
        return QualifiedS015AuthorityDecision(
            database_alias=result.database_alias,
            actor_pk=result.actor_pk,
            actor_identity_id=result.actor_identity_id,
            actor_access_epoch=result.actor_access_epoch,
            action=result.action,
            command_subtype=result.command_subtype,
            target_fingerprint=result.target_fingerprint,
            request_reference=result.request_reference,
            idempotency_key=result.idempotency_key,
            evaluated_at=result.evaluated_at,
            active_membership_head_reference=(
                result.active_membership_head_reference if post_founding else None
            ),
            contextual_role_head_references=(
                result.contextual_role_head_references if post_founding else ()
            ),
            authority_reference=authority_reference,
            authority_decision_reference=decision_reference,
        )


__all__ = [
    "QualifiedS015AuthorityDecision",
    "S015Authority",
    "S015AuthorityDenied",
    "S015AuthorityError",
    "S015AuthorityMalformed",
    "S015AuthorityProvider",
    "S015AuthorityRefusalCode",
    "S015AuthorityUnavailable",
    "S015PostFoundingAuthorityRefusal",
    "S015PostFoundingAuthorityRequest",
    "S015PostFoundingAuthorityResponse",
    "S015PreFoundingAuthorityRefusal",
    "S015PreFoundingAuthorityRequest",
    "S015PreFoundingAuthorityResponse",
    "authority_target_fingerprint",
    "canonical_json_bytes",
    "canonical_timestamp",
]