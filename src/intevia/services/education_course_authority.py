from __future__ import annotations

import inspect
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import connections

from src.intevia.services.education_course_contract import (
    EducationCourseAction,
    EducationCourseAuthorityProvider,
    EducationCourseAuthorityRefusal,
    EducationCourseAuthorityRequest,
    EducationCourseAuthorityResponse,
    EducationCourseRefusalCode,
    authority_decision_reference,
    canonical_timestamp,
)


class EducationCourseError(Exception):
    pass


class EducationCourseAuthorityUnavailable(EducationCourseError):
    pass


class EducationCourseAuthorityMalformed(EducationCourseError):
    pass


class EducationCourseAuthorityDenied(EducationCourseError):
    pass


@dataclass(frozen=True, slots=True)
class QualifiedEducationCourseAuthorityDecision:
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
    authority_decision_reference: str


def _canonical_reference(value: object, name: str, maximum: int) -> str:
    if type(value) is not str:
        raise EducationCourseAuthorityMalformed(f"{name} must be a string")
    canonical = unicodedata.normalize("NFC", value).strip()
    if canonical != value or not canonical or len(canonical) > maximum:
        raise EducationCourseAuthorityMalformed(f"{name} is not canonical")
    return canonical


def _validate_request(request: EducationCourseAuthorityRequest) -> None:
    if type(request) is not EducationCourseAuthorityRequest:
        raise EducationCourseAuthorityMalformed("authority request type is invalid")
    if type(request.database_alias) is not str or not request.database_alias:
        raise EducationCourseAuthorityMalformed("database_alias is invalid")
    if type(request.actor_pk) is not int or request.actor_pk < 1:
        raise EducationCourseAuthorityMalformed("actor_pk is invalid")
    if type(request.actor_identity_id) is not UUID:
        raise EducationCourseAuthorityMalformed("actor_identity_id is invalid")
    if type(request.actor_access_epoch) is not int or request.actor_access_epoch < 0:
        raise EducationCourseAuthorityMalformed("actor_access_epoch is invalid")
    if type(request.action) is not EducationCourseAction:
        raise EducationCourseAuthorityMalformed("action is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", request.target_fingerprint or "") is None:
        raise EducationCourseAuthorityMalformed("target_fingerprint is invalid")
    _canonical_reference(request.request_reference, "request_reference", 128)
    _canonical_reference(request.idempotency_key, "idempotency_key", 120)
    try:
        canonical_timestamp(request.evaluated_at)
    except (TypeError, ValueError) as exc:
        raise EducationCourseAuthorityMalformed("evaluated_at is invalid") from exc


def _validate_result(
    result: EducationCourseAuthorityResponse | EducationCourseAuthorityRefusal,
) -> None:
    if type(result.database_alias) is not str or not result.database_alias:
        raise EducationCourseAuthorityMalformed("database_alias is invalid")
    if type(result.actor_pk) is not int or result.actor_pk < 1:
        raise EducationCourseAuthorityMalformed("actor_pk is invalid")
    if type(result.actor_identity_id) is not UUID:
        raise EducationCourseAuthorityMalformed("actor_identity_id is invalid")
    if type(result.actor_access_epoch) is not int or result.actor_access_epoch < 0:
        raise EducationCourseAuthorityMalformed("actor_access_epoch is invalid")
    if type(result.action) is not EducationCourseAction:
        raise EducationCourseAuthorityMalformed("action is invalid")
    if (
        type(result.target_fingerprint) is not str
        or re.fullmatch(r"[0-9a-f]{64}", result.target_fingerprint) is None
    ):
        raise EducationCourseAuthorityMalformed("target_fingerprint is invalid")
    _canonical_reference(result.request_reference, "request_reference", 128)
    _canonical_reference(result.idempotency_key, "idempotency_key", 120)
    try:
        canonical_timestamp(result.evaluated_at)
    except (TypeError, ValueError) as exc:
        raise EducationCourseAuthorityMalformed("evaluated_at is invalid") from exc
    if type(result) is EducationCourseAuthorityRefusal:
        if type(result.refusal_code) is not EducationCourseRefusalCode:
            raise EducationCourseAuthorityMalformed("refusal code is invalid")
    else:
        _canonical_reference(
            result.authority_reference, "authority_reference", 255
        )


class EducationCourseAuthority:
    def __init__(
        self,
        *,
        provider: EducationCourseAuthorityProvider,
        database_alias: str = "default",
    ) -> None:
        method = getattr(provider, "evaluate_course_definition", None)
        if provider is None or not callable(method):
            raise TypeError("provider must implement evaluate_course_definition")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.provider = provider
        self.database_alias = database_alias

    def qualify(
        self, request: EducationCourseAuthorityRequest
    ) -> QualifiedEducationCourseAuthorityDecision:
        _validate_request(request)
        if request.database_alias != self.database_alias:
            raise EducationCourseAuthorityMalformed("database alias mismatch")
        if not connections[self.database_alias].in_atomic_block:
            raise EducationCourseAuthorityMalformed("outer atomic transaction required")
        try:
            result = self.provider.evaluate_course_definition(request)
        except Exception as exc:
            raise EducationCourseAuthorityUnavailable(
                "course definition authority unavailable"
            ) from exc
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise EducationCourseAuthorityMalformed("authority response must be synchronous")
        allowed_types = (
            EducationCourseAuthorityResponse,
            EducationCourseAuthorityRefusal,
        )
        if type(result) not in allowed_types:
            raise EducationCourseAuthorityMalformed("authority response type is invalid")
        _validate_result(result)
        echoed = (
            "database_alias",
            "actor_pk",
            "actor_identity_id",
            "actor_access_epoch",
            "action",
            "target_fingerprint",
            "request_reference",
            "idempotency_key",
            "evaluated_at",
        )
        if any(getattr(result, field) != getattr(request, field) for field in echoed):
            raise EducationCourseAuthorityMalformed("authority response echo mismatch")
        if type(result) is EducationCourseAuthorityRefusal:
            raise EducationCourseAuthorityDenied("course definition authority denied")
        authority_reference = _canonical_reference(
            result.authority_reference, "authority_reference", 255
        )
        try:
            decision_reference = authority_decision_reference(result)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise EducationCourseAuthorityMalformed(
                "authority response canonicalization failed"
            ) from exc
        return QualifiedEducationCourseAuthorityDecision(
            database_alias=result.database_alias,
            actor_pk=result.actor_pk,
            actor_identity_id=result.actor_identity_id,
            actor_access_epoch=result.actor_access_epoch,
            action=result.action,
            target_fingerprint=result.target_fingerprint,
            request_reference=result.request_reference,
            idempotency_key=result.idempotency_key,
            evaluated_at=result.evaluated_at,
            authority_reference=authority_reference,
            authority_decision_reference=decision_reference,
        )


__all__ = [
    "EducationCourseAuthority",
    "EducationCourseAuthorityDenied",
    "EducationCourseError",
    "EducationCourseAuthorityMalformed",
    "EducationCourseAuthorityUnavailable",
    "QualifiedEducationCourseAuthorityDecision",
]