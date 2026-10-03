"""Shared substrate for the S015 organism command services (PKT-B slices S1 and S2).

The helpers S1 and S2 both need, lifted from S1's membership service with their
bodies unchanged: canonical input validation, the organism command mutex, the
command payload fingerprint, the temporal-basis shape, the empty-anchor
fingerprint, actor and actor-context locking, the authority request builder,
and the S1 receipt-replay machinery (S2's own row names its own module).

The baseline's shared substrate `qualify_completed_replay` /
`QualifiedOrganismCommandReplay` lives in
src/intevia/services/organism_membership_service.py and is untouched by this
module (the Reviewer's F1; commission PKT-B S2 §2).
"""
from __future__ import annotations

import hashlib
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from django.contrib.auth.models import User
from django.db import connections, transaction

from core.models import (
    ContextualRoleAssignment,
    Identity,
    OrganismCommandReceipt,
    OrganismMembership,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import (
    QualifiedS015AuthorityDecision,
    S015Authority,
    S015AuthorityDenied,
    S015PostFoundingAuthorityRequest,
    authority_target_fingerprint,
    canonical_json_bytes,
    canonical_timestamp,
)


_MUTEX_DOMAIN = b"INTEVIA:S015:ORGANISM_COMMAND_MUTEX:v1\x00"


class S015CommandSubstrateError(Exception):
    pass


class S015CommandAtomicityRequired(S015CommandSubstrateError):
    pass


class S015CommandMutexUnavailable(S015CommandSubstrateError):
    pass


class S015CommandReplayMismatch(S015CommandSubstrateError):
    pass


@contextmanager
def authority_atomic(database_alias: str):
    refusal = None
    with transaction.atomic(using=database_alias):
        try:
            yield
        except S015AuthorityDenied as exc:
            refusal = exc
    if refusal is not None:
        raise refusal


def _canonical_key(value: object, name: str, maximum: int) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    canonical = unicodedata.normalize("NFC", value).strip()
    if canonical != value or not canonical or len(canonical) > maximum:
        raise ValueError(f"{name} is not canonical")
    return canonical


def _canonical_uuid(value: object, name: str) -> UUID:
    if type(value) is not UUID:
        raise TypeError(f"{name} must be a UUID")
    return value


def _validate_timestamp(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _resulting_temporal_basis(
    *,
    payload_fingerprint: str,
    effective_at: datetime,
    received_at: datetime,
) -> tuple[str | None, str | None]:
    if effective_at == received_at:
        return None, None
    kind = "RETROSPECTIVE" if effective_at < received_at else "PROSPECTIVE"
    return kind, f"s015tb1:{payload_fingerprint}"


def _command_payload(
    *,
    domain: bytes,
    schema: str,
    action: OrganismAction,
    actor_identity_id: UUID,
    actor_access_epoch: int,
    request_reference: str,
    idempotency_key: str,
    occurred_at: datetime,
    target: dict[str, object],
    command: dict[str, object],
) -> str:
    payload = {
        "action": action.value,
        "actor_access_epoch": actor_access_epoch,
        "actor_identity_id": str(actor_identity_id),
        "command": command,
        "idempotency_key": idempotency_key,
        "occurred_at": canonical_timestamp(occurred_at),
        "request_reference": request_reference,
        "schema": schema,
        "target": target,
    }
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


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


def _empty_anchor_fingerprint(
    *,
    database_alias: str,
    anchor_token: str,
    anchor_uuid: UUID,
) -> str:
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            "SELECT public.s015_digest_envelope(public.s015_canonical_envelope(%s, %s::uuid, '{}'::text[]))",
            [anchor_token, anchor_uuid],
        )
        return cursor.fetchone()[0]


def _lock_actor(
    *,
    database_alias: str,
    credential: User,
    unavailable: type[Exception],
) -> Identity:
    try:
        identity = (
            Identity.objects.using(database_alias)
            .select_for_update()
            .select_related("credential")
            .get(credential=credential)
        )
    except Identity.DoesNotExist as exc:
        raise unavailable("actor Identity not found") from exc
    if identity.access_state != Identity.AccessState.ACTIVE:
        raise unavailable("actor Identity is not active")
    if not identity.credential.is_active:
        raise unavailable("actor credential is not active")
    return identity


def _lock_actor_membership_context(
    *,
    database_alias: str,
    actor_identity_id: UUID,
    living_organism_id: UUID,
    unavailable: type[Exception],
) -> tuple[OrganismMembership, tuple[str, ...]]:
    try:
        membership = (
            OrganismMembership.objects.using(database_alias)
            .select_for_update()
            .get(identity__identity_id=actor_identity_id, living_organism__organism_id=living_organism_id)
        )
    except OrganismMembership.DoesNotExist as exc:
        raise unavailable(
            "actor active membership is required"
        ) from exc
    if membership.state != "ACTIVE" or membership.head_transition_id is None:
        raise unavailable(
            "actor active membership is required"
        )
    role_refs = tuple(
        assignment.head_state_event.lineage_reference
        for assignment in (
            ContextualRoleAssignment.objects.using(database_alias)
            .select_for_update()
            .select_related("head_state_event")
            .filter(membership=membership, state="ACTIVE", head_state_event__isnull=False)
            .order_by("pk")
        )
    )
    return membership, role_refs


def _authority_request(
    *,
    database_alias: str,
    actor: Identity,
    actor_membership: OrganismMembership,
    actor_role_head_refs: tuple[str, ...],
    action: OrganismAction,
    request_reference: str,
    idempotency_key: str,
    received_at: datetime,
    target_fingerprint: str,
    effective_at: datetime | None = None,
    target: dict[str, object] | None = None,
    living_organism_id: UUID | None = None,
    assignment_uuid: UUID | None = None,
    outcome: str | None = None,
    version_uuid: UUID | None = None,
    version_kind: str | None = None,
) -> S015PostFoundingAuthorityRequest:
    return S015PostFoundingAuthorityRequest(
        database_alias=database_alias,
        actor_pk=actor.pk,
        actor_identity_id=actor.identity_id,
        actor_access_epoch=actor.access_epoch,
        action=action,
        command_subtype=action.value,
        target_fingerprint=target_fingerprint,
        request_reference=request_reference,
        idempotency_key=idempotency_key,
        evaluated_at=received_at,
        active_membership_head_reference=actor_membership.head_transition.lineage_reference,
        contextual_role_head_references=actor_role_head_refs,
        effective_at=effective_at,
        target=target,
        living_organism_id=living_organism_id,
        assignment_uuid=assignment_uuid,
        outcome=outcome,
        version_uuid=version_uuid,
        version_kind=version_kind,
    )


class _OrganismCommandService:
    """Replay, authority and finalise, shared by the S1 and S2 command services.

    The subclass declares `_action`, `_not_found` and `_authority_unavailable`,
    and implements `_replay_result` for its own result row shape.
    """

    _action: OrganismAction
    _not_found: type[Exception]
    _authority_unavailable: type[Exception]

    def __init__(
        self,
        *,
        authority: S015Authority,
        clock: Callable[[], datetime],
        database_alias: str = "default",
    ) -> None:
        if not isinstance(authority, S015Authority):
            raise TypeError("authority must be an S015Authority")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        if authority.database_alias != database_alias:
            raise ValueError("authority database_alias mismatch")
        self._authority = authority
        self._clock = clock
        self._alias = database_alias

    def _replay_result(self, payload: object) -> object:
        raise NotImplementedError

    def _lock_existing_receipt(
        self,
        *,
        actor: Identity,
        request_reference: str,
        payload_fingerprint: str,
        idempotency_key: str,
    ) -> OrganismCommandReceipt | None:
        receipt = (
            OrganismCommandReceipt.objects.using(self._alias)
            .select_for_update()
            .filter(
                actor=actor,
                action=self._action.value,
                idempotency_key=idempotency_key,
            )
            .first()
        )
        if receipt is None:
            return None
        if (
            receipt.request_reference != request_reference
            or receipt.payload_fingerprint != payload_fingerprint
        ):
            raise S015CommandReplayMismatch("completed receipt does not match request")
        return receipt

    def _evaluate_authority(
        self,
        *,
        actor: Identity,
        actor_membership: OrganismMembership,
        actor_role_refs: tuple[str, ...],
        request_reference: str,
        idempotency_key: str,
        target_fingerprint: str,
        received_at: datetime,
        effective_at: datetime | None = None,
        target: dict[str, object] | None = None,
        living_organism_id: UUID | None = None,
        assignment_uuid: UUID | None = None,
    ) -> QualifiedS015AuthorityDecision:
        request = _authority_request(
            database_alias=self._alias,
            actor=actor,
            actor_membership=actor_membership,
            actor_role_head_refs=actor_role_refs,
            action=self._action,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            received_at=received_at,
            target_fingerprint=target_fingerprint,
            effective_at=effective_at,
            target=target,
            living_organism_id=living_organism_id,
            assignment_uuid=assignment_uuid,
        )
        return self._authority.qualify(request)

    def _finalise(
        self,
        *,
        actor: Identity,
        request_reference: str,
        idempotency_key: str,
        payload_fingerprint: str,
        result,
    ):
        receipt = OrganismCommandReceipt.objects.using(self._alias).create(
            actor=actor,
            action=self._action.value,
            idempotency_key=idempotency_key,
            request_reference=request_reference,
            payload_fingerprint=payload_fingerprint,
            authority_decision_reference=result.authority_decision_reference,
            lineage_reference=result.lineage_reference,
            result_payload=result.receipt_payload(),
        )
        return self._replay_result(receipt.result_payload)

    def _attempt_replay(
        self,
        *,
        actor: Identity,
        request_reference: str,
        payload_fingerprint: str,
        idempotency_key: str,
    ):
        receipt = self._lock_existing_receipt(
            actor=actor,
            request_reference=request_reference,
            payload_fingerprint=payload_fingerprint,
            idempotency_key=idempotency_key,
        )
        if receipt is None:
            return None
        return self._replay_result(receipt.result_payload)


def _founding_provenance_assignment_id(
    *,
    database_alias: str,
    assignment_id: int,
    living_organism_id: int,
) -> int | None:
    """The landed founding-provenance test (the Human Governor's IL-7 ruling, 29 Sep).

    An assignment is of founding provenance when its sequence-1 event was written by
    the LO's own founding transaction: the landed completion guard
    (s015_guard_founding_completion_update, 0021) admits the founder and basis into
    the LO only inside the transaction whose id the LO's insert stamped into
    `founding_insert_xid` (xid8, holding pg_current_xact_id()'s value), and an office
    whose first event carries that same transaction as its writing transaction is of
    that founding. The test reads the office's first EVENT, not its anchor: event rows
    are never updated (s015_refuse_row_mutation), so the sequence-1 event's xmin is
    permanently its inserting transaction, while xmin on the anchor is the transaction
    that wrote the row's current version — every head advance and every cache refresh
    replaces it. The anchor must also be held by the founder's own membership for the
    COORDINATOR role. Returns the assignment id when the provenance holds, else None.

    Limits: PostgreSQL provides no cast from xid to xid8 (its cast catalogue holds
    only xid8 -> xid, explicit), so the comparison runs in the direction that exists —
    32-bit transaction ids. A 32-bit xid repeats after about four billion
    transactions, so an office whose first event was written exactly that many
    transactions after the founding would also match; that would over-protect (it
    withholds an ending) and would never record anything false. And within a single
    transaction the test cannot distinguish the founding office from another office
    the same founding transaction inserted on the founder's membership (the landed
    founding shape writes exactly one), so the reading is the commission's: the
    founder's original founding COORDINATOR assignment is the one whose first event
    the founding transaction wrote.
    """
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            """
            SELECT a.id
              FROM public.core_contextualroleassignment a
              JOIN public.core_organismmembership m
                ON m.id = a.membership_id
              JOIN public.core_livingorganism lo
                ON lo.id = m.living_organism_id
              JOIN public.core_organismroledefinition r
                ON r.id = a.role_definition_id
             WHERE a.id = %s
               AND m.living_organism_id = %s
               AND r.code = 'COORDINATOR'
               AND lo.founder_identity_id IS NOT NULL
               AND m.identity_id = lo.founder_identity_id
               AND lo.founding_insert_xid IS NOT NULL
               AND EXISTS (
                     SELECT 1 FROM public.core_contextualroleassignmentstateevent e
                      WHERE e.assignment_id = a.id
                        AND e.sequence = 1
                        AND e.xmin = lo.founding_insert_xid::xid
                   )
            """,
            [assignment_id, living_organism_id],
        )
        row = cursor.fetchone()
    return None if row is None else int(row[0])


__all__ = [
    "QualifiedS015AuthorityDecision",
    "S015CommandAtomicityRequired",
    "S015CommandMutexUnavailable",
    "S015CommandReplayMismatch",
    "S015CommandSubstrateError",
    "_OrganismCommandService",
    "_authority_request",
    "_canonical_key",
    "_canonical_uuid",
    "_command_payload",
    "_empty_anchor_fingerprint",
    "_founding_provenance_assignment_id",
    "_lock_actor",
    "_lock_actor_membership_context",
    "_resulting_temporal_basis",
    "_validate_timestamp",
    "acquire_organism_command_mutex",
    "authority_target_fingerprint",
    "canonical_json_bytes",
    "canonical_timestamp",
    "command_mutex_key",
]
