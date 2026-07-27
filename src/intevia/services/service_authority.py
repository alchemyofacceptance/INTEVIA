"""SERVICE-owned authority contract for S012 Activity commands."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Protocol
import unicodedata
from uuid import UUID

from django.db import connections

from core.models import ServiceActivityTransition


ServiceCommandAction = ServiceActivityTransition.Action
_DECISION_DOMAIN = b"INTEVIA:S012:SERVICE_COMMAND_AUTHORITY_DECISION:v1\x00"
_DECISION_SCHEMA = "intevia.s012.authority-decision.v1"


class ServiceCommandNotAuthorised(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class ServiceCommandAuthorityRequest:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: ServiceCommandAction
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class ServiceCommandAuthorityResponse:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: ServiceCommandAction
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    evaluated_at: datetime
    authority_reference: str


class ServiceCommandAuthorityProvider(Protocol):
    def authorise(
        self,
        *,
        request: ServiceCommandAuthorityRequest,
    ) -> ServiceCommandAuthorityResponse | None: ...


@dataclass(frozen=True, slots=True)
class QualifiedServiceCommandDecision:
    database_alias: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    action: ServiceCommandAction
    target_fingerprint: str
    request_reference: str
    idempotency_key: str
    authority_reference: str
    evaluated_at: datetime
    decision_reference: str


def canonical_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_json_bytes(values: dict[str, object]) -> bytes:
    if type(values) is not dict:
        raise TypeError("canonical value must be an object")
    for name, value in values.items():
        if type(name) is not str or unicodedata.normalize("NFC", name) != name:
            raise ValueError("canonical field name is invalid")
        if type(value) is float:
            raise ValueError("floats are outside the canonical profile")
        if type(value) is str and unicodedata.normalize("NFC", value) != value:
            raise ValueError(f"{name} is not NFC text")
    return json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_decision_bytes(response: ServiceCommandAuthorityResponse) -> bytes:
    values = asdict(response)
    values.update(
        {
            "schema": _DECISION_SCHEMA,
            "actor_identity_id": str(response.actor_identity_id),
            "action": response.action.value,
            "evaluated_at": canonical_timestamp(response.evaluated_at),
        }
    )
    return canonical_json_bytes(values)


def decision_reference_for(response: ServiceCommandAuthorityResponse) -> str:
    digest = hashlib.sha256(
        _DECISION_DOMAIN + canonical_decision_bytes(response)
    ).hexdigest()
    return f"s012d1:{digest}"


class ServiceCommandAuthority:
    def __init__(
        self,
        *,
        provider: ServiceCommandAuthorityProvider,
        database_alias: str = "default",
    ) -> None:
        if provider is None or not callable(getattr(provider, "authorise", None)):
            raise TypeError("provider must implement authorise")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self.provider = provider
        self.database_alias = database_alias

    def qualify(
        self,
        *,
        request: ServiceCommandAuthorityRequest,
    ) -> QualifiedServiceCommandDecision:
        if request.database_alias != self.database_alias:
            raise ServiceCommandNotAuthorised("database alias mismatch")
        if not connections[self.database_alias].in_atomic_block:
            raise ServiceCommandNotAuthorised("outer atomic transaction is required")
        response = self.provider.authorise(request=request)
        if type(response) is not ServiceCommandAuthorityResponse:
            raise ServiceCommandNotAuthorised("authority response is unavailable")
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
        if any(getattr(response, name) != getattr(request, name) for name in echoed):
            raise ServiceCommandNotAuthorised("authority response mismatch")
        authority_reference = _bounded_reference(
            response.authority_reference,
            "authority_reference",
            255,
        )
        if authority_reference != response.authority_reference:
            raise ServiceCommandNotAuthorised("authority response is not canonical")
        reference = decision_reference_for(response)
        return QualifiedServiceCommandDecision(
            database_alias=response.database_alias,
            actor_pk=response.actor_pk,
            actor_identity_id=response.actor_identity_id,
            actor_access_epoch=response.actor_access_epoch,
            action=response.action,
            target_fingerprint=response.target_fingerprint,
            request_reference=response.request_reference,
            idempotency_key=response.idempotency_key,
            authority_reference=authority_reference,
            evaluated_at=response.evaluated_at,
            decision_reference=reference,
        )


def _bounded_reference(value: str, name: str, maximum: int) -> str:
    if type(value) is not str:
        raise ServiceCommandNotAuthorised(f"{name} is invalid")
    canonical = value.strip()
    if (
        not canonical
        or len(canonical) > maximum
        or unicodedata.normalize("NFC", canonical) != canonical
    ):
        raise ServiceCommandNotAuthorised(f"{name} is invalid")
    return canonical


__all__ = [
    "QualifiedServiceCommandDecision",
    "ServiceCommandAction",
    "ServiceCommandAuthority",
    "ServiceCommandAuthorityProvider",
    "ServiceCommandAuthorityRequest",
    "ServiceCommandAuthorityResponse",
    "ServiceCommandNotAuthorised",
    "canonical_decision_bytes",
    "canonical_json_bytes",
    "canonical_timestamp",
    "decision_reference_for",
]