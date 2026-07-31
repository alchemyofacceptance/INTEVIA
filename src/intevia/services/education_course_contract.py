from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol
from uuid import UUID

from django.contrib.auth.models import User


CONTRACT_VERSION = 1
AUTHORITY_TARGET_SCHEMA = "intevia.s014.education-course.authority-target.v1"
COMMAND_SCHEMA = "intevia.s014.education-course.command.v1"
AUTHORITY_DECISION_SCHEMA = "intevia.s014.education-course.authority-decision.v1"
LINEAGE_SCHEMA = "intevia.s014.education-course.lineage.v1"

_TARGET_DOMAIN = b"INTEVIA:S014:EDUCATION_COURSE_AUTHORITY_TARGET:v1\x00"
_COMMAND_DOMAIN = b"INTEVIA:S014:EDUCATION_COURSE_COMMAND:v1\x00"
_DECISION_DOMAIN = b"INTEVIA:S014:EDUCATION_COURSE_AUTHORITY_DECISION:v1\x00"
_LINEAGE_DOMAIN = b"INTEVIA:S014:EDUCATION_COURSE_LINEAGE:v1\x00"


class EducationCourseAction(str, Enum):
    CREATE = "CREATE"
    APPEND_VERSION = "APPEND_VERSION"


class EducationCourseRefusalCode(str, Enum):
    DENIED = "DENIED"


@dataclass(frozen=True, slots=True)
class CreateCourseCommand:
    credential: User
    course_id: UUID
    course_name: str
    course_description: str
    course_learning_objectives: str
    definition_basis_reference: str
    request_reference: str
    idempotency_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AppendCourseVersionCommand:
    credential: User
    course_id: UUID
    expected_current_version_pk: int
    expected_current_lineage_reference: str
    course_name: str
    course_description: str
    course_learning_objectives: str
    definition_basis_reference: str
    request_reference: str
    idempotency_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class EducationCourseAuthorityRequest:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: EducationCourseAction
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class EducationCourseAuthorityResponse:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: EducationCourseAction
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    authority_reference: str


@dataclass(frozen=True, slots=True)
class EducationCourseAuthorityRefusal:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: EducationCourseAction
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    refusal_code: EducationCourseRefusalCode


class EducationCourseAuthorityProvider(Protocol):
    def evaluate_course_definition(
        self, request: EducationCourseAuthorityRequest
    ) -> EducationCourseAuthorityResponse | EducationCourseAuthorityRefusal: ...


@dataclass(frozen=True, slots=True)
class CreateCourseReceipt:
    database_alias: str
    course_id: UUID
    course_version_pk: int
    version_number: int
    lineage_reference: str
    request_reference: str
    idempotency_key: str
    payload_fingerprint: str
    occurred_at: datetime
    replayed: bool


@dataclass(frozen=True, slots=True)
class AppendCourseVersionReceipt:
    database_alias: str
    course_id: UUID
    course_version_pk: int
    version_number: int
    predecessor_version_pk: int
    predecessor_lineage_reference: str
    lineage_reference: str
    request_reference: str
    idempotency_key: str
    payload_fingerprint: str
    occurred_at: datetime
    replayed: bool


def canonical_timestamp(value: datetime) -> str:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
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
        canonical: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str or unicodedata.normalize("NFC", key) != key:
                raise ValueError("object keys must be NFC strings")
            if key in canonical:
                raise ValueError("duplicate object key")
            canonical[key] = _canonicalise(item)
        return canonical
    raise TypeError("unsupported canonical primitive")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _canonicalise(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def authority_target(
    *,
    database_alias: str,
    action: EducationCourseAction,
    course_id: UUID,
    expected_current_version_pk: int | None = None,
    expected_current_lineage_reference: str | None = None,
) -> dict[str, object]:
    target: dict[str, object] = {
        "schema": AUTHORITY_TARGET_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "database_alias": database_alias,
        "action": action,
        "course_id": course_id,
    }
    if action is EducationCourseAction.APPEND_VERSION:
        target["expected_current_version_pk"] = expected_current_version_pk
        target["expected_current_lineage_reference"] = (
            expected_current_lineage_reference
        )
    return target


def target_fingerprint(target: dict[str, object]) -> str:
    return hashlib.sha256(_TARGET_DOMAIN + canonical_json_bytes(target)).hexdigest()


def command_payload(
    *,
    database_alias: str,
    action: EducationCourseAction,
    actor_identity_id: UUID,
    actor_access_epoch: int,
    target: dict[str, object],
    course_name: str,
    course_description: str,
    course_learning_objectives: str,
    definition_basis_reference: str,
    request_reference: str,
    idempotency_key: str,
    occurred_at: datetime,
) -> dict[str, object]:
    return {
        "schema": COMMAND_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "database_alias": database_alias,
        "action": action,
        "actor_identity_id": actor_identity_id,
        "actor_access_epoch": actor_access_epoch,
        "target": target,
        "definition": {
            "course_name": course_name,
            "course_description": course_description,
            "course_learning_objectives": course_learning_objectives,
        },
        "definition_basis_reference": definition_basis_reference,
        "request_reference": request_reference,
        "idempotency_key": idempotency_key,
        "occurred_at": occurred_at,
    }


def payload_fingerprint(payload: dict[str, object]) -> str:
    return hashlib.sha256(_COMMAND_DOMAIN + canonical_json_bytes(payload)).hexdigest()


def authority_decision_payload(
    response: EducationCourseAuthorityResponse,
) -> dict[str, object]:
    return {
        "schema": AUTHORITY_DECISION_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "database_alias": response.database_alias,
        "actor_pk": response.actor_pk,
        "actor_identity_id": response.actor_identity_id,
        "actor_access_epoch": response.actor_access_epoch,
        "action": response.action,
        "target_fingerprint": response.target_fingerprint,
        "request_reference": response.request_reference,
        "idempotency_key": response.idempotency_key,
        "evaluated_at": response.evaluated_at,
        "authority_reference": response.authority_reference,
    }


def authority_decision_reference(response: EducationCourseAuthorityResponse) -> str:
    digest = hashlib.sha256(
        _DECISION_DOMAIN + canonical_json_bytes(authority_decision_payload(response))
    ).hexdigest()
    return "s014d1:" + digest


def lineage_payload(
    *,
    database_alias: str,
    course_id: UUID,
    version_number: int,
    predecessor_lineage_reference: str | None,
    action: EducationCourseAction,
    actor_identity_id: UUID,
    actor_access_epoch: int,
    authority_decision_reference: str,
    request_reference: str,
    idempotency_key: str,
    payload_fingerprint: str,
    occurred_at: datetime,
) -> dict[str, object]:
    return {
        "schema": LINEAGE_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "database_alias": database_alias,
        "course_id": course_id,
        "version_number": version_number,
        "predecessor_lineage_reference": predecessor_lineage_reference,
        "action": action,
        "actor_identity_id": actor_identity_id,
        "actor_access_epoch": actor_access_epoch,
        "authority_decision_reference": authority_decision_reference,
        "request_reference": request_reference,
        "idempotency_key": idempotency_key,
        "payload_fingerprint": payload_fingerprint,
        "occurred_at": occurred_at,
    }


def lineage_reference(payload: dict[str, object]) -> str:
    digest = hashlib.sha256(_LINEAGE_DOMAIN + canonical_json_bytes(payload)).hexdigest()
    return "s014l1:" + digest