from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import connections, transaction

from core.models import (
    ContextualRoleAssignment,
    AuthorityBasis,
    Identity,
    LivingOrganism,
    OrganismCommandReceipt,
    OrganismMembership,
    OrganismMembershipTransition,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import (
    QualifiedS015AuthorityDecision,
    S015Authority,
    S015AuthorityDenied,
    S015AuthorityMalformed,
    S015AuthorityUnavailable,
    S015PostFoundingAuthorityRequest,
    authority_target_fingerprint,
    canonical_json_bytes,
    canonical_timestamp,
)


_MUTEX_DOMAIN = b"INTEVIA:S015:ORGANISM_COMMAND_MUTEX:v1\x00"
_COMMAND_DOMAIN = b"INTEVIA:S015:ORGANISM_MEMBERSHIP_COMMAND:v1\x00"
_TARGET_SCHEMA = "intevia.s015.organism-membership-target.v1"
_COMMAND_SCHEMA = "intevia.s015.organism-membership-command.v1"
_LINEAGE_SCHEMA = "intevia.s015.organism-membership-lineage.v1"
_ACTION = OrganismAction.TRANSITION_MEMBERSHIP
_OPEN_STATE = "PROPOSED"
_STATE_UNUSED = frozenset({"SUSPENDED", "RESTRICTED", "PRIVACY_TRANSFORMED"})
_STATE_ALLOWED = {
    "PROPOSED": frozenset({"ACTIVE", "PROBATIONARY", "ENDED"}),
    "PROBATIONARY": frozenset({"ACTIVE", "ENDED"}),
    "ACTIVE": frozenset({"ENDED"}),
    "ENDED": frozenset({"PROPOSED"}),
}


class S015CommandSubstrateError(Exception):
    pass


class S015CommandAtomicityRequired(S015CommandSubstrateError):
    pass


class S015CommandMutexUnavailable(S015CommandSubstrateError):
    pass


class S015CommandReplayMismatch(S015CommandSubstrateError):
    pass


class S015MembershipCommandError(Exception):
    pass


class S015MembershipAuthorityUnavailable(S015MembershipCommandError):
    pass


class S015MembershipExists(S015MembershipCommandError):
    pass


class S015MembershipNotFound(S015MembershipCommandError):
    pass


class S015MembershipEdgeRefused(S015MembershipCommandError):
    pass


class S015MembershipStateUnused(S015MembershipCommandError):
    pass


class S015OperationNotYetImplemented(S015MembershipCommandError):
    def __init__(self, operation: str, limit_id: str, owning_slice: str):
        super().__init__(
            f"{operation} is supported by the design but not yet built ({limit_id}, {owning_slice})"
        )
        self.operation = operation
        self.limit_id = limit_id
        self.owning_slice = owning_slice


@dataclass(frozen=True, slots=True)
class OpenMembershipCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime
    living_organism_id: UUID
    member_identity_id: UUID
    reason_class: str


@dataclass(frozen=True, slots=True)
class TransitionMembershipCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime
    membership_uuid: UUID
    resulting_state: str
    reason_class: str


@dataclass(frozen=True, slots=True)
class MembershipCommandResult:
    membership_uuid: UUID
    membership_id: int
    resulting_state: str
    transition_id: int
    transition_sequence: int
    lineage_reference: str
    temporal_basis_kind: str | None
    temporal_basis_reference: str | None
    current_state_effective_at: datetime
    state_known_at: datetime
    state_source_event_set_fingerprint: str
    authority_reference: str
    authority_decision_reference: str


def _canonical_key(value: object, name: str, maximum: int) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    canonical = unicodedata.normalize("NFC", value).strip()
    if canonical != value or not canonical or len(canonical) > maximum:
        raise ValueError(f"{name} is not canonical")
    return canonical


def _canonical_reason(value: object) -> str:
    return _canonical_key(value, "reason_class", 48)


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


def _insert_membership_transition_row(
    *,
    database_alias: str,
    event_uuid: UUID,
    membership_id: int,
    sequence: int,
    predecessor_id: int | None,
    predecessor_sequence: int | None,
    action: str,
    actor_id: int,
    actor_state: str,
    actor_capacity: str,
    enterer_identity_id: int,
    entry_mode: str,
    composing_rule_state: str,
    composing_rule_reference: str | None,
    composer_state: str,
    composer_identity_id: int | None,
    effective_until: datetime | None,
    effective_until_state: str,
    l1_commitment: str | None,
    l2_commitment: str | None,
    actor_access_epoch: int,
    authority_basis_id: int,
    authority_decision_reference: str,
    evidence_reference: str,
    request_reference: str,
    idempotency_key: str,
    payload_fingerprint: str,
    lineage_reference: str,
    temporal_basis_kind: str | None,
    temporal_basis_reference: str | None,
    occurred_at: datetime,
    effective_at: datetime,
    received_at: datetime,
    prior_state: str | None,
    resulting_state: str,
    reason_class: str,
) -> int:
    with connections[database_alias].cursor() as cursor:
        if predecessor_id is None:
            predecessor_l1_commitment = None
        else:
            cursor.execute(
                "SELECT l1_commitment FROM public.core_organismmembershiptransition WHERE id = %s",
                [predecessor_id],
            )
            predecessor_row = cursor.fetchone()
            predecessor_l1_commitment = None if predecessor_row is None else predecessor_row[0]
        cursor.execute(
            "SELECT public.s015_0022_digest('s015p1', public.s015_0022_parts_preimage(%s, %s::uuid))",
            ["core_organismmembershiptransition", event_uuid],
        )
        parts_commitment = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO public.core_organismmembershiptransition (
                event_uuid,
                membership_id,
                sequence,
                predecessor_id,
                predecessor_sequence,
                action,
                actor_id,
                actor_state,
                actor_capacity,
                enterer_identity_id,
                entry_mode,
                composing_rule_state,
                composing_rule_reference,
                composer_state,
                composer_identity_id,
                effective_until,
                effective_until_state,
                l1_commitment,
                predecessor_l1_commitment,
                l2_commitment,
                parts_commitment,
                actor_access_epoch,
                authority_basis_id,
                authority_decision_reference,
                evidence_reference,
                request_reference,
                idempotency_key,
                payload_fingerprint,
                lineage_reference,
                temporal_basis_kind,
                temporal_basis_reference,
                occurred_at,
                effective_at,
                received_at,
                prior_state,
                resulting_state,
                reason_class
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            ) RETURNING id
            """,
            [
                event_uuid,
                membership_id,
                sequence,
                predecessor_id,
                predecessor_sequence,
                action,
                actor_id,
                actor_state,
                actor_capacity,
                enterer_identity_id,
                entry_mode,
                composing_rule_state,
                composing_rule_reference,
                composer_state,
                composer_identity_id,
                effective_until,
                effective_until_state,
                l1_commitment,
                predecessor_l1_commitment,
                l2_commitment,
                parts_commitment,
                actor_access_epoch,
                authority_basis_id,
                authority_decision_reference,
                evidence_reference,
                request_reference,
                idempotency_key,
                payload_fingerprint,
                lineage_reference,
                temporal_basis_kind,
                temporal_basis_reference,
                occurred_at,
                effective_at,
                received_at,
                prior_state,
                resulting_state,
                reason_class,
            ],
        )
        return cursor.fetchone()[0]


def _parse_canonical_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise TypeError("timestamp payload must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _command_payload(
    *,
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
        "schema": _COMMAND_SCHEMA,
        "target": target,
    }
    return hashlib.sha256(_COMMAND_DOMAIN + canonical_json_bytes(payload)).hexdigest()


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
) -> Identity:
    try:
        identity = (
            Identity.objects.using(database_alias)
            .select_for_update()
            .select_related("credential")
            .get(credential=credential)
        )
    except Identity.DoesNotExist as exc:
        raise S015MembershipAuthorityUnavailable("actor Identity not found") from exc
    if identity.access_state != Identity.AccessState.ACTIVE:
        raise S015MembershipAuthorityUnavailable("actor Identity is not active")
    if not identity.credential.is_active:
        raise S015MembershipAuthorityUnavailable("actor credential is not active")
    return identity


def _lock_actor_membership_context(
    *,
    database_alias: str,
    actor_identity_id: UUID,
    living_organism_id: UUID,
) -> tuple[OrganismMembership, tuple[str, ...]]:
    try:
        membership = (
            OrganismMembership.objects.using(database_alias)
            .select_for_update()
            .get(identity__identity_id=actor_identity_id, living_organism__organism_id=living_organism_id)
        )
    except OrganismMembership.DoesNotExist as exc:
        raise S015MembershipAuthorityUnavailable(
            "actor active membership is required"
        ) from exc
    if membership.state != "ACTIVE" or membership.head_transition_id is None:
        raise S015MembershipAuthorityUnavailable(
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
    request_reference: str,
    idempotency_key: str,
    received_at: datetime,
    target_fingerprint: str,
) -> S015PostFoundingAuthorityRequest:
    return S015PostFoundingAuthorityRequest(
        database_alias=database_alias,
        actor_pk=actor.pk,
        actor_identity_id=actor.identity_id,
        actor_access_epoch=actor.access_epoch,
        action=_ACTION,
        command_subtype=_ACTION.value,
        target_fingerprint=target_fingerprint,
        request_reference=request_reference,
        idempotency_key=idempotency_key,
        evaluated_at=received_at,
        active_membership_head_reference=actor_membership.head_transition.lineage_reference,
        contextual_role_head_references=actor_role_head_refs,
    )


def _target_fingerprint_open(
    *,
    member_identity_id: UUID,
    living_organism_id: UUID,
    reason_class: str,
) -> str:
    return authority_target_fingerprint(
        {
            "action": _ACTION.value,
            "living_organism_id": str(living_organism_id),
            "member_identity_id": str(member_identity_id),
            "reason_class": reason_class,
            "resulting_state": _OPEN_STATE,
            "schema": _TARGET_SCHEMA,
        }
    )


def _target_fingerprint_transition(
    *,
    membership_uuid: UUID,
    living_organism_id: UUID,
    resulting_state: str,
    reason_class: str,
) -> str:
    return authority_target_fingerprint(
        {
            "action": _ACTION.value,
            "living_organism_id": str(living_organism_id),
            "membership_uuid": str(membership_uuid),
            "reason_class": reason_class,
            "resulting_state": resulting_state,
            "schema": _TARGET_SCHEMA,
        }
    )


def _expected_replay_result(
    payload: object,
) -> MembershipCommandResult:
    if type(payload) is not dict:
        raise S015CommandReplayMismatch("completed receipt does not match request")
    try:
        return MembershipCommandResult(
            membership_uuid=UUID(payload["membership_uuid"]),
            membership_id=int(payload["membership_id"]),
            resulting_state=str(payload["resulting_state"]),
            transition_id=int(payload["transition_id"]),
            transition_sequence=int(payload["transition_sequence"]),
            lineage_reference=str(payload["lineage_reference"]),
            temporal_basis_kind=payload.get("temporal_basis_kind"),
            temporal_basis_reference=payload.get("temporal_basis_reference"),
            current_state_effective_at=_parse_canonical_timestamp(payload["current_state_effective_at"]),
            state_known_at=_parse_canonical_timestamp(payload["state_known_at"]),
            state_source_event_set_fingerprint=str(payload["state_source_event_set_fingerprint"]),
            authority_reference=str(payload["authority_reference"]),
            authority_decision_reference=str(payload["authority_decision_reference"]),
        )
    except Exception as exc:  # pragma: no cover - defensive
        raise S015CommandReplayMismatch("completed receipt does not match request") from exc


def _result_payload(result: MembershipCommandResult) -> dict[str, object]:
    return {
        "authority_decision_reference": result.authority_decision_reference,
        "authority_reference": result.authority_reference,
        "current_state_effective_at": canonical_timestamp(result.current_state_effective_at),
        "lineage_reference": result.lineage_reference,
        "membership_id": result.membership_id,
        "membership_uuid": str(result.membership_uuid),
        "resulting_state": result.resulting_state,
        "state_known_at": canonical_timestamp(result.state_known_at),
        "state_source_event_set_fingerprint": result.state_source_event_set_fingerprint,
        "temporal_basis_kind": result.temporal_basis_kind,
        "temporal_basis_reference": result.temporal_basis_reference,
        "transition_id": result.transition_id,
        "transition_sequence": result.transition_sequence,
    }


def _result_from_membership(
    *,
    membership: OrganismMembership,
    transition: OrganismMembershipTransition,
    authority_reference: str,
    authority_decision_reference: str,
) -> MembershipCommandResult:
    return MembershipCommandResult(
        membership_uuid=membership.membership_uuid,
        membership_id=membership.pk,
        resulting_state=transition.resulting_state,
        transition_id=transition.pk,
        transition_sequence=transition.sequence,
        lineage_reference=transition.lineage_reference,
        temporal_basis_kind=transition.temporal_basis_kind,
        temporal_basis_reference=transition.temporal_basis_reference,
        current_state_effective_at=membership.current_state_effective_at,
        state_known_at=membership.state_known_at,
        state_source_event_set_fingerprint=membership.state_source_event_set_fingerprint,
        authority_reference=authority_reference,
        authority_decision_reference=authority_decision_reference,
    )


def _validate_member_state(value: object) -> str:
    state = _canonical_key(value, "resulting_state", 19)
    if state in _STATE_UNUSED:
        raise S015MembershipStateUnused(
            f"membership state {state} is unused by v1.0"
        )
    if state not in {"PROPOSED", "ACTIVE", "PROBATIONARY", "ENDED"}:
        raise S015MembershipEdgeRefused(f"membership edge {state} is not decided")
    return state


def _validate_edge(current_state: str, target_state: str, membership: OrganismMembership) -> None:
    if current_state in _STATE_UNUSED or target_state in _STATE_UNUSED:
        raise S015MembershipStateUnused(
            f"membership state {current_state}->{target_state} is unused by v1.0"
        )
    if current_state == "ENDED" and target_state == "PROPOSED":
        raise S015OperationNotYetImplemented("rejoin", "IL-1", "S4")
    if target_state == "ENDED" and current_state in {"ACTIVE", "PROBATIONARY"}:
        if membership.contextual_role_assignments.filter(state="ACTIVE").exists():
            raise S015OperationNotYetImplemented("leave_with_office", "IL-2", "S2")
    allowed = _STATE_ALLOWED.get(current_state)
    if allowed is None or target_state not in allowed:
        raise S015MembershipEdgeRefused(
            f"membership edge {current_state} -> {target_state} is refused"
        )


class OrganismMembershipService:
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
                action=_ACTION.value,
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
    ) -> QualifiedS015AuthorityDecision:
        request = _authority_request(
            database_alias=self._alias,
            actor=actor,
            actor_membership=actor_membership,
            actor_role_head_refs=actor_role_refs,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            received_at=received_at,
            target_fingerprint=target_fingerprint,
        )
        return self._authority.qualify(request)

    def _finalise(
        self,
        *,
        actor: Identity,
        request_reference: str,
        idempotency_key: str,
        payload_fingerprint: str,
        result: MembershipCommandResult,
    ) -> MembershipCommandResult:
        receipt = OrganismCommandReceipt.objects.using(self._alias).create(
            actor=actor,
            action=_ACTION.value,
            idempotency_key=idempotency_key,
            request_reference=request_reference,
            payload_fingerprint=payload_fingerprint,
            authority_decision_reference=result.authority_decision_reference,
            lineage_reference=result.lineage_reference,
            result_payload=_result_payload(result),
        )
        return _expected_replay_result(receipt.result_payload)

    def _attempt_replay(
        self,
        *,
        actor: Identity,
        request_reference: str,
        payload_fingerprint: str,
        idempotency_key: str,
    ) -> MembershipCommandResult | None:
        receipt = self._lock_existing_receipt(
            actor=actor,
            request_reference=request_reference,
            payload_fingerprint=payload_fingerprint,
            idempotency_key=idempotency_key,
        )
        if receipt is None:
            return None
        return _expected_replay_result(receipt.result_payload)

    def open_membership(self, command: OpenMembershipCommand) -> MembershipCommandResult:
        with transaction.atomic(using=self._alias):
            return self._execute_open(command)

    def _execute_open(self, command: OpenMembershipCommand) -> MembershipCommandResult:
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        reason_class = _canonical_reason(command.reason_class)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(command.received_at, "received_at")
        living_organism_id = _canonical_uuid(command.living_organism_id, "living_organism_id")
        member_identity_id = _canonical_uuid(command.member_identity_id, "member_identity_id")

        actor = _lock_actor(database_alias=self._alias, credential=command.credential)
        acquire_organism_command_mutex(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            action=_ACTION,
            idempotency_key=idempotency_key,
        )

        try:
            organism = (
                LivingOrganism.objects.using(self._alias)
                .select_for_update()
                .get(organism_id=living_organism_id)
            )
        except LivingOrganism.DoesNotExist as exc:
            raise S015MembershipNotFound("living organism not found") from exc
        try:
            target_identity = (
                Identity.objects.using(self._alias)
                .select_for_update()
                .get(identity_id=member_identity_id)
            )
        except Identity.DoesNotExist as exc:
            raise S015MembershipNotFound("target identity not found") from exc
        actor_membership, actor_role_refs = _lock_actor_membership_context(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            living_organism_id=living_organism_id,
        )

        target_fingerprint = _target_fingerprint_open(
            member_identity_id=member_identity_id,
            living_organism_id=living_organism_id,
            reason_class=reason_class,
        )
        command_fingerprint = _command_payload(
            action=_ACTION,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            target={
                "living_organism_id": str(living_organism_id),
                "member_identity_id": str(member_identity_id),
                "reason_class": reason_class,
                "resulting_state": _OPEN_STATE,
                "schema": _TARGET_SCHEMA,
            },
            command={
                "living_organism_id": str(living_organism_id),
                "member_identity_id": str(member_identity_id),
                "reason_class": reason_class,
                "resulting_state": _OPEN_STATE,
                "effective_at": canonical_timestamp(effective_at),
                "received_at": canonical_timestamp(received_at),
                "schema": _COMMAND_SCHEMA,
            },
        )

        replay = self._attempt_replay(
            actor=actor,
            request_reference=request_reference,
            payload_fingerprint=command_fingerprint,
            idempotency_key=idempotency_key,
        )
        if replay is not None:
            return replay

        basis_id = organism.founding_authority_basis_id
        if basis_id is None:
            raise S015MembershipAuthorityUnavailable(
                "organism founding authority basis is required"
            )
        basis = (
            AuthorityBasis.objects.using(self._alias)
            .select_for_update()
            .get(pk=basis_id)
        )
        if (
            OrganismMembership.objects.using(self._alias)
            .select_for_update()
            .filter(identity=target_identity, living_organism=organism)
            .exists()
        ):
            raise S015MembershipExists("membership already exists")

        decision = self._evaluate_authority(
            actor=actor,
            actor_membership=actor_membership,
            actor_role_refs=actor_role_refs,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            target_fingerprint=target_fingerprint,
            received_at=received_at,
        )

        membership_uuid = uuid4()
        membership_event_uuid = uuid4()
        membership = OrganismMembership.objects.using(self._alias).create(
            membership_uuid=membership_uuid,
            identity=target_identity,
            living_organism=organism,
            state=_OPEN_STATE,
            current_state_effective_at=effective_at,
            state_known_at=received_at,
            state_source_event_set_fingerprint=_empty_anchor_fingerprint(
                database_alias=self._alias,
                anchor_token="core_organismmembership",
                anchor_uuid=membership_uuid,
            ),
        )
        transition_id = _insert_membership_transition_row(
            database_alias=self._alias,
            event_uuid=membership_event_uuid,
            membership_id=membership.pk,
            sequence=1,
            predecessor_id=None,
            predecessor_sequence=None,
            action=_ACTION.value,
            actor_id=actor.pk,
            actor_state="PARTY",
            actor_capacity="STANDING",
            enterer_identity_id=actor.pk,
            entry_mode="TRANSPORT",
            composing_rule_state="NA_TRANSPORT",
            composing_rule_reference=None,
            composer_state="NA_TRANSPORT",
            composer_identity_id=None,
            effective_until=None,
            effective_until_state="NONE",
            l1_commitment=None,
            l2_commitment=None,
            actor_access_epoch=actor.access_epoch,
            authority_basis_id=basis.pk,
            authority_decision_reference=decision.authority_decision_reference,
            evidence_reference=decision.authority_reference,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=command_fingerprint,
            lineage_reference=f"s015l1:{hashlib.sha256(_LINEAGE_SCHEMA.encode('utf-8') + canonical_json_bytes({
                'action': _ACTION.value,
                'actor_access_epoch': actor.access_epoch,
                'actor_identity_id': str(actor.identity_id),
                'effective_at': canonical_timestamp(effective_at),
                'occurred_at': canonical_timestamp(occurred_at),
                'payload_fingerprint': command_fingerprint,
                'reason_class': reason_class,
                'received_at': canonical_timestamp(received_at),
                'resulting_state': _OPEN_STATE,
                'schema': _LINEAGE_SCHEMA,
                'sequence': 1,
            })).hexdigest()}",
            temporal_basis_kind=_resulting_temporal_basis(
                payload_fingerprint=command_fingerprint,
                effective_at=effective_at,
                received_at=received_at,
            )[0],
            temporal_basis_reference=_resulting_temporal_basis(
                payload_fingerprint=command_fingerprint,
                effective_at=effective_at,
                received_at=received_at,
            )[1],
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            prior_state=None,
            resulting_state=_OPEN_STATE,
            reason_class=reason_class,
        )
        membership.refresh_from_db(using=self._alias)

        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                ["core_organismmembership", membership.pk, effective_at],
            )
        membership.refresh_from_db(using=self._alias)
        transition = OrganismMembershipTransition.objects.using(self._alias).get(pk=transition_id)
        transition.refresh_from_db(using=self._alias)

        result = _result_from_membership(
            membership=membership,
            transition=transition,
            authority_reference=decision.authority_reference,
            authority_decision_reference=decision.authority_decision_reference,
        )
        return self._finalise(
            actor=actor,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=command_fingerprint,
            result=result,
        )

    def transition_membership(
        self,
        command: TransitionMembershipCommand,
    ) -> MembershipCommandResult:
        with transaction.atomic(using=self._alias):
            return self._execute_transition(command)

    def _execute_transition(
        self,
        command: TransitionMembershipCommand,
    ) -> MembershipCommandResult:
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        reason_class = _canonical_reason(command.reason_class)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(command.received_at, "received_at")
        membership_uuid = _canonical_uuid(command.membership_uuid, "membership_uuid")
        target_state = _validate_member_state(command.resulting_state)

        actor = _lock_actor(database_alias=self._alias, credential=command.credential)
        acquire_organism_command_mutex(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            action=_ACTION,
            idempotency_key=idempotency_key,
        )

        try:
            membership = (
                OrganismMembership.objects.using(self._alias)
                .select_for_update()
                .select_related("identity", "living_organism")
                .get(membership_uuid=membership_uuid)
            )
        except OrganismMembership.DoesNotExist as exc:
            raise S015MembershipNotFound("membership not found") from exc
        actor_membership, actor_role_refs = _lock_actor_membership_context(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            living_organism_id=membership.living_organism.organism_id,
        )

        target_fingerprint = _target_fingerprint_transition(
            membership_uuid=membership_uuid,
            living_organism_id=membership.living_organism.organism_id,
            resulting_state=target_state,
            reason_class=reason_class,
        )
        command_fingerprint = _command_payload(
            action=_ACTION,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            target={
                "living_organism_id": str(membership.living_organism.organism_id),
                "membership_uuid": str(membership_uuid),
                "reason_class": reason_class,
                "resulting_state": target_state,
                "schema": _TARGET_SCHEMA,
            },
            command={
                "membership_uuid": str(membership_uuid),
                "reason_class": reason_class,
                "resulting_state": target_state,
                "effective_at": canonical_timestamp(effective_at),
                "received_at": canonical_timestamp(received_at),
                "schema": _COMMAND_SCHEMA,
            },
        )

        replay = self._attempt_replay(
            actor=actor,
            request_reference=request_reference,
            payload_fingerprint=command_fingerprint,
            idempotency_key=idempotency_key,
        )
        if replay is not None:
            return replay

        current_state = _canonical_key(membership.state, "membership.state", 19)
        _validate_edge(current_state, target_state, membership)

        head_transition = None
        if membership.head_transition_id is not None:
            head_transition = (
                OrganismMembershipTransition.objects.using(self._alias)
                .only("effective_at", "occurred_at", "received_at")
                .get(pk=membership.head_transition_id)
            )
            if (effective_at, occurred_at, received_at) < (
                head_transition.effective_at,
                head_transition.occurred_at,
                head_transition.received_at,
            ):
                raise S015OperationNotYetImplemented("out_of_order_transition", "IL-3", "S5")

        decision = self._evaluate_authority(
            actor=actor,
            actor_membership=actor_membership,
            actor_role_refs=actor_role_refs,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            target_fingerprint=target_fingerprint,
            received_at=received_at,
        )

        basis_id = membership.living_organism.founding_authority_basis_id
        if basis_id is None:
            raise S015MembershipAuthorityUnavailable(
                "organism founding authority basis is required"
            )
        basis = (
            AuthorityBasis.objects.using(self._alias)
            .select_for_update()
            .get(pk=basis_id)
        )
        predecessor = head_transition
        sequence = 1 if predecessor is None else predecessor.sequence + 1
        temporal_basis_kind, temporal_basis_reference = _resulting_temporal_basis(
            payload_fingerprint=command_fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )
        transition_event_uuid = uuid4()
        lineage_reference = f"s015l1:{hashlib.sha256(_LINEAGE_SCHEMA.encode('utf-8') + canonical_json_bytes({
            'action': _ACTION.value,
            'actor_access_epoch': actor.access_epoch,
            'actor_identity_id': str(actor.identity_id),
            'effective_at': canonical_timestamp(effective_at),
            'membership_uuid': str(membership_uuid),
            'occurred_at': canonical_timestamp(occurred_at),
            'payload_fingerprint': command_fingerprint,
            'reason_class': reason_class,
            'received_at': canonical_timestamp(received_at),
            'resulting_state': target_state,
            'schema': _LINEAGE_SCHEMA,
            'sequence': sequence,
        })).hexdigest()}"

        transition_id = _insert_membership_transition_row(
            database_alias=self._alias,
            event_uuid=transition_event_uuid,
            membership_id=membership.pk,
            sequence=sequence,
            predecessor_id=predecessor.pk if predecessor is not None else None,
            predecessor_sequence=predecessor.sequence if predecessor is not None else None,
            action=_ACTION.value,
            actor_id=actor.pk,
            actor_state="PARTY",
            actor_capacity="STANDING",
            enterer_identity_id=actor.pk,
            entry_mode="TRANSPORT",
            composing_rule_state="NA_TRANSPORT",
            composing_rule_reference=None,
            composer_state="NA_TRANSPORT",
            composer_identity_id=None,
            effective_until=None,
            effective_until_state="NONE",
            l1_commitment=None,
            l2_commitment=None,
            actor_access_epoch=actor.access_epoch,
            authority_basis_id=basis.pk,
            authority_decision_reference=decision.authority_decision_reference,
            evidence_reference=decision.authority_reference,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=command_fingerprint,
            lineage_reference=lineage_reference,
            temporal_basis_kind=temporal_basis_kind,
            temporal_basis_reference=temporal_basis_reference,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            prior_state=current_state,
            resulting_state=target_state,
            reason_class=reason_class,
        )

        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                ["core_organismmembership", membership.pk, effective_at],
            )
        membership.refresh_from_db()
        transition = OrganismMembershipTransition.objects.using(self._alias).get(pk=transition_id)
        transition.refresh_from_db()

        result = _result_from_membership(
            membership=membership,
            transition=transition,
            authority_reference=decision.authority_reference,
            authority_decision_reference=decision.authority_decision_reference,
        )
        return self._finalise(
            actor=actor,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=command_fingerprint,
            result=result,
        )


QualifiedOrganismCommandReplay = MembershipCommandResult


def qualify_completed_replay(
    *,
    database_alias: str,
    actor_identity_id: UUID,
    action: OrganismAction,
    idempotency_key: str,
    request_reference: str,
    payload_fingerprint: str,
    authority_decision_reference: str,
) -> MembershipCommandResult | None:
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
    return _expected_replay_result(receipt.result_payload)


__all__ = [
    "MembershipCommandResult",
    "OpenMembershipCommand",
    "OrganismMembershipService",
    "QualifiedOrganismCommandReplay",
    "QualifiedS015AuthorityDecision",
    "S015CommandAtomicityRequired",
    "S015CommandMutexUnavailable",
    "S015CommandReplayMismatch",
    "S015CommandSubstrateError",
    "S015MembershipAuthorityUnavailable",
    "S015MembershipCommandError",
    "S015MembershipEdgeRefused",
    "S015MembershipExists",
    "S015MembershipNotFound",
    "S015MembershipStateUnused",
    "S015OperationNotYetImplemented",
    "TransitionMembershipCommand",
    "acquire_organism_command_mutex",
    "command_mutex_key",
    "qualify_completed_replay",
]