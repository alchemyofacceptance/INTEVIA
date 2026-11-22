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
    """Raised when SERVICE refuses command authority.

    The module raises this PermissionError with a reason string.
    Handlers for PermissionError or OSError also catch it.
    """
    pass


@dataclass(frozen=True, slots=True)
class ServiceCommandAuthorityRequest:
    """The facts a provider is asked to authorise.

    Frozen, with slots. Nothing here checks a field's type
    or value. qualify requires the provider's response to
    echo all nine fields equal.
    """
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
    """A provider's answer: the request echoed, plus a reference.

    Frozen, with slots. authority_reference is the
    provider's own reference for its decision. Nothing here
    checks a field. qualify accepts only this exact type,
    and all ten fields go into the decision reference.
    """
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
    """The interface a SERVICE authority provider implements.

    A typing Protocol, not checked at run time:
    ServiceCommandAuthority requires only that the provider
    has a callable authorise attribute.
    """
    def authorise(
        self,
        *,
        request: ServiceCommandAuthorityRequest,
    ) -> ServiceCommandAuthorityResponse | None:
        """Return an authority response for qualification, or None.

        After its alias and atomic checks, qualify calls this method
        once with request by keyword. None or any result other than
        an exact ServiceCommandAuthorityResponse causes a refusal.
        qualify then checks the response. Provider exceptions escape.
        """
        ...


@dataclass(frozen=True, slots=True)
class QualifiedServiceCommandDecision:
    """The decision qualify returns after a provider allows.

    Frozen, with slots. The fields are the response's, and
    decision_reference is decision_reference_for of that
    response: s012d1: and a hex SHA-256. Note the order:
    authority_reference comes before evaluated_at here,
    unlike the response.
    """
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
    """Return an aware datetime as UTC text.

    Raise ValueError unless the value is a datetime with tzinfo
    and a UTC offset. Convert to UTC, then format with strftime
    as %Y-%m-%dT%H:%M:%S.%fZ, with six microsecond digits.
    """
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_json_bytes(values: dict[str, object]) -> bytes:
    """Return compact, sorted JSON as UTF-8 bytes.

    Require an exact dict or raise TypeError. For each top-level
    pair in input order, require an exact NFC str key, reject an
    exact float value, then require NFC for an exact str value.
    Each violation raises ValueError. These checks do not
    descend into containers or normalise text.

    json.dumps sorts keys at each level, uses compact separators
    and preserves non-ASCII text. Nested finite floats and
    non-NFC text can pass. Booleans and None are supported.
    Encoder errors propagate, including
    ValueError for NaN, infinity or a cycle and TypeError for an
    unsupported value or unsortable keys. The final UTF-8 encode
    can raise UnicodeEncodeError for an unpaired surrogate.
    """
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
    """Return the canonical JSON bytes of an authority response.

    Copy the dataclass fields with asdict. A non-dataclass
    instance such as a dict raises TypeError. For the declared
    response the object has ten fields, plus the schema tag
    intevia.s012.authority-decision.v1. Convert actor_identity_id
    with str(), take action.value, then format evaluated_at with
    canonical_timestamp. Keep the other fields from asdict.

    There is no exact response, UUID or action type check here.
    A missing attribute raises AttributeError. An invalid or
    naive timestamp raises ValueError. Copy and conversion
    errors propagate, as do all errors from canonical_json_bytes
    when it encodes the resulting object.
    """
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
    """Return s012d1: followed by 64 lowercase SHA-256 hex digits.

    Hash these bytes in order: the ASCII domain
    INTEVIA:S012:SERVICE_COMMAND_AUTHORITY_DECISION:v1, one NUL
    byte, then canonical_decision_bytes(response).
    The printable prefix is added after hashing. The bytes cover
    the schema and all ten response fields, including the
    provider's reference. Equal canonical bytes give equal
    references, even across different representations.

    This is an unkeyed digest. It makes no authority decision
    and authenticates no provider. Encoding errors propagate.
    """
    digest = hashlib.sha256(
        _DECISION_DOMAIN + canonical_decision_bytes(response)
    ).hexdigest()
    return f"s012d1:{digest}"


class ServiceCommandAuthority:
    """Qualify SERVICE command responses for one database alias.

    Ask the injected provider inside an atomic block. Return a
    QualifiedServiceCommandDecision when its response passes the
    echo, reference and encoding steps. Explicit refusals raise
    ServiceCommandNotAuthorised. Other errors can propagate.
    This class performs no row reads, locks or writes of its own.
    """
    def __init__(
        self,
        *,
        provider: ServiceCommandAuthorityProvider,
        database_alias: str = "default",
    ) -> None:
        """Bind a provider and database alias, defaulting to "default".

        First require a callable authorise attribute or raise
        TypeError. Then require a nonempty exact str for the alias
        or raise ValueError. Store both without looking up the
        alias. At qualify, a matching but unknown alias raises
        Django's ConnectionDoesNotExist.
        """
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
        """Qualify one provider response and return its decision.

        First require matching database aliases. Look up the alias,
        then require its connection's in_atomic_block flag. Any
        depth counts. Call provider.authorise(request=request) once.
        Require an exact ServiceCommandAuthorityResponse. Compare
        its nine echoed fields to the request in declaration order
        using Python inequality, stopping at the first mismatch.

        Require an exact str authority_reference. Strip it, check
        for empty text, length over 255, then non-NFC text. Refuse
        if stripping changed an otherwise valid reference. Hash the
        response with decision_reference_for and return its fields
        and that reference in a QualifiedServiceCommandDecision.

        Explicit refusals raise ServiceCommandNotAuthorised. An
        unknown matching alias raises Django's ConnectionDoesNotExist.
        Errors from the provider, field access or comparison escape,
        as do TypeError, ValueError and other encoding failures.
        The request type and echoed field types are unchecked. A
        plain string action can match and then raise AttributeError
        on .value. The qualifier performs no writes of its own.
        """
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
    """Return the stripped reference or raise an authority refusal.

    Require an exact str. Strip the ends and refuse an empty
    result, one longer than maximum, or one outside NFC.
    Each refusal raises ServiceCommandNotAuthorised with the
    field name. Interior whitespace and controls are preserved.
    """
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