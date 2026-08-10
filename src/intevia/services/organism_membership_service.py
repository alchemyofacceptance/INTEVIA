from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from django.db import connections

from core.models import Identity, OrganismCommandReceipt
from core.organism_contract import OrganismAction


_MUTEX_DOMAIN = b"INTEVIA:S015:ORGANISM_COMMAND_MUTEX:v1\x00"


class S015CommandSubstrateError(Exception):
    pass


class S015CommandAtomicityRequired(S015CommandSubstrateError):
    pass


class S015CommandMutexUnavailable(S015CommandSubstrateError):
    pass


class S015CommandReplayMismatch(S015CommandSubstrateError):
    pass


@dataclass(frozen=True, slots=True)
class QualifiedOrganismCommandReplay:
    database_alias: str
    receipt_pk: int
    actor_identity_id: UUID
    action: OrganismAction
    idempotency_key: str
    request_reference: str
    payload_fingerprint: str
    authority_decision_reference: str
    lineage_reference: str
    result_payload: object


def _canonical_key(value: object, name: str, maximum: int) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    canonical = unicodedata.normalize("NFC", value).strip()
    if canonical != value or not canonical or len(canonical) > maximum:
        raise ValueError(f"{name} is not canonical")
    return canonical


def command_mutex_key(
    *,
    actor_identity_id: UUID,
    action: OrganismAction,
    idempotency_key: str,
) -> int:
    if type(actor_identity_id) is not UUID:
        raise TypeError("actor_identity_id must be a UUID")
    if type(action) is not OrganismAction:
        raise TypeError("action must be an OrganismAction")
    canonical_key = _canonical_key(idempotency_key, "idempotency_key", 120)
    digest = hashlib.sha256(
        _MUTEX_DOMAIN
        + actor_identity_id.bytes
        + b"\x00"
        + action.value.encode("ascii")
        + b"\x00"
        + canonical_key.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def acquire_organism_command_mutex(
    *,
    database_alias: str,
    actor_identity_id: UUID,
    action: OrganismAction,
    idempotency_key: str,
) -> int:
    if type(database_alias) is not str or not database_alias:
        raise ValueError("database_alias is required")
    connection = connections[database_alias]
    if not connection.in_atomic_block:
        raise S015CommandAtomicityRequired("outer atomic transaction required")
    if connection.vendor != "postgresql":
        raise S015CommandMutexUnavailable(
            "S015 advisory mutex requires PostgreSQL"
        )
    lock_key = command_mutex_key(
        actor_identity_id=actor_identity_id,
        action=action,
        idempotency_key=idempotency_key,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])
    return lock_key


def qualify_completed_replay(
    *,
    database_alias: str,
    actor_identity_id: UUID,
    action: OrganismAction,
    idempotency_key: str,
    request_reference: str,
    payload_fingerprint: str,
    authority_decision_reference: str,
) -> QualifiedOrganismCommandReplay | None:
    connection = connections[database_alias]
    if not connection.in_atomic_block:
        raise S015CommandAtomicityRequired("outer atomic transaction required")
    actor = Identity.objects.using(database_alias).filter(
        identity_id=actor_identity_id
    ).only("pk", "identity_id").first()
    if actor is None:
        return None
    receipt = (
        OrganismCommandReceipt.objects.using(database_alias)
        .select_for_update()
        .filter(
            actor_id=actor.pk,
            action=action.value,
            idempotency_key=idempotency_key,
        )
        .first()
    )
    if receipt is None:
        return None
    expected = (
        _canonical_key(request_reference, "request_reference", 128),
        payload_fingerprint,
        authority_decision_reference,
    )
    observed = (
        receipt.request_reference,
        receipt.payload_fingerprint,
        receipt.authority_decision_reference,
    )
    if observed != expected:
        raise S015CommandReplayMismatch("completed receipt does not match request")
    return QualifiedOrganismCommandReplay(
        database_alias=database_alias,
        receipt_pk=receipt.pk,
        actor_identity_id=actor.identity_id,
        action=action,
        idempotency_key=receipt.idempotency_key,
        request_reference=receipt.request_reference,
        payload_fingerprint=receipt.payload_fingerprint,
        authority_decision_reference=receipt.authority_decision_reference,
        lineage_reference=receipt.lineage_reference,
        result_payload=receipt.result_payload,
    )


__all__ = [
    "QualifiedOrganismCommandReplay",
    "S015CommandAtomicityRequired",
    "S015CommandMutexUnavailable",
    "S015CommandReplayMismatch",
    "S015CommandSubstrateError",
    "acquire_organism_command_mutex",
    "command_mutex_key",
    "qualify_completed_replay",
]