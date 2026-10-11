from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError, connections, transaction

from core.models import (
    AuthorityBasis,
    CircleParticipation,
    CircleParticipationEnd,
    ContextualRoleAssignment,
    Identity,
    GovernedRule,
    LivingOrganism,
    OrganismCommandReceipt,
    OrganismMembership,
    OrganismMembershipTransition,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import (
    QualifiedS015AuthorityDecision,
    S015AuthorityRefusalCode,
    S015AuthorityMalformed,
    S015AuthorityUnavailable,
    authority_target_fingerprint,
    canonical_json_bytes,
    canonical_timestamp,
)
from src.intevia.services.organism_command_support import (
    ReplayViewer,
    S015CommandAtomicityRequired,
    S015CommandMutexUnavailable,
    S015CommandReplayMismatch,
    S015CommandSubstrateError,
    _canonical_key,
    _canonical_uuid,
    _command_payload,
    _empty_anchor_fingerprint,
    _lock_actor,
    _lock_actor_membership_context,
    _lock_registered_organism,
    _OrganismCommandService,
    _resulting_temporal_basis,
    _validate_timestamp,
    WithheldField,
    acquire_organism_command_mutex,
    authority_atomic,
    command_mutex_key,
)


_COMMAND_DOMAIN = b"INTEVIA:S015:ORGANISM_MEMBERSHIP_COMMAND:v1\x00"
_TARGET_SCHEMA = "intevia.s015.organism-membership-target.v1"
_COMMAND_SCHEMA = "intevia.s015.organism-membership-command.v1"
_LINEAGE_SCHEMA = "intevia.s015.organism-membership-lineage.v1"
_DEPARTURE_COMPOSITION_RULE_UUID = UUID("52345b7a-2fab-45ce-954e-e3ff922a8a37")
# Interim S1 mapping from the Human Governor (28 Sep); HD-B7 is still open and this is not final vocabulary.
_ACTION = OrganismAction.TRANSITION_MEMBERSHIP
_OPEN_STATE = "PROPOSED"
_STATE_UNUSED = frozenset({"SUSPENDED", "RESTRICTED", "PRIVACY_TRANSFORMED"})
_STATE_ALLOWED = {
    "PROPOSED": frozenset({"ACTIVE", "PROBATIONARY", "ENDED"}),
    "PROBATIONARY": frozenset({"ACTIVE", "ENDED"}),
    "ACTIVE": frozenset({"ENDED"}),
    "ENDED": frozenset({"PROPOSED"}),
}


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


class S015MembershipRefused(S015MembershipCommandError):
    def __init__(self, message: str, *, refusal_code: S015AuthorityRefusalCode) -> None:
        super().__init__(message)
        self.refusal_code = refusal_code


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
    membership_uuid: UUID | WithheldField
    resulting_state: str | WithheldField
    transition_sequence: int | WithheldField
    lineage_reference: str
    temporal_basis_kind: str | None
    temporal_basis_reference: str | None
    current_state_effective_at: datetime | WithheldField
    state_known_at: datetime | WithheldField
    state_source_event_set_fingerprint: str | WithheldField
    authority_reference: str | WithheldField
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, object]:
        return _result_payload(self)

    @classmethod
    def replay_field_audiences(cls, result: "MembershipCommandResult") -> dict[str, str]:
        fields = {
            "resulting_state": "SUBJECT",
            "transition_sequence": "SUBJECT",
            "current_state_effective_at": "SUBJECT",
            "state_known_at": "SUBJECT",
            "state_source_event_set_fingerprint": "SUBJECT",
        }
        if result.transition_sequence == 1:
            fields["membership_uuid"] = "SUBJECT"
        return fields


_OWN_MEMBERSHIP_STATES = frozenset({"ACTIVE", "PROBATIONARY", "RESTRICTED", "SUSPENDED"})


@dataclass(frozen=True, slots=True)
class OwnMembershipView:
    organism_id: UUID
    state: str
    current_state_effective_at: datetime = field(
        metadata={"label": "the beginning of the current state"},
    )
    offices: tuple[tuple[str, str], ...]


class OrganismMembershipReader:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        database_alias: str = "default",
    ) -> None:
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self._clock = clock
        self._alias = database_alias

    def list_own_memberships(self, reader: Identity) -> list[OwnMembershipView]:
        reader_identity_id = self._reader_identity_id(reader)
        now = self._now()
        memberships = OrganismMembership.objects.using(self._alias).filter(
            identity__identity_id=reader_identity_id,
            state__in=_OWN_MEMBERSHIP_STATES,
            current_state_effective_at__lte=now,
            state_known_at__lte=now,
        ).select_related("living_organism").order_by("living_organism__organism_id")
        return [self._view(membership, now) for membership in memberships]

    def read_own_membership(
        self,
        reader: Identity,
        membership_uuid: UUID,
    ) -> OwnMembershipView:
        reader_identity_id = self._reader_identity_id(reader)
        membership_uuid = _canonical_uuid(membership_uuid, "membership_uuid")
        now = self._now()
        membership = OrganismMembership.objects.using(self._alias).filter(
            membership_uuid=membership_uuid,
            identity__identity_id=reader_identity_id,
            state__in=_OWN_MEMBERSHIP_STATES,
            current_state_effective_at__lte=now,
            state_known_at__lte=now,
        ).select_related("living_organism").first()
        if membership is None:
            raise S015MembershipNotFound("membership not found")
        return self._view(membership, now)

    @staticmethod
    def _reader_identity_id(reader: Identity) -> UUID:
        if not isinstance(reader, Identity) or type(reader.identity_id) is not UUID:
            raise TypeError("reader must be an Identity")
        return reader.identity_id

    def _now(self) -> datetime:
        if self._clock is not None:
            return _validate_timestamp(self._clock(), "clock")
        with connections[self._alias].cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            return cursor.fetchone()[0]

    def _view(self, membership: OrganismMembership, now: datetime) -> OwnMembershipView:
        from src.intevia.services.organism_role_service import assignment_in_effect

        offices = ContextualRoleAssignment.objects.using(self._alias).filter(
            membership=membership,
            state="ACTIVE",
        ).select_related("role_definition").order_by(
            "role_definition__code", "role_definition__scope", "pk",
        )
        office_roles = tuple(
            (office.role_definition.code, office.role_definition.scope)
            for office in offices
            if assignment_in_effect(office, now, database_alias=self._alias)
        )
        return OwnMembershipView(
            organism_id=membership.living_organism.organism_id,
            state=membership.state,
            current_state_effective_at=membership.current_state_effective_at,
            offices=office_roles,
        )


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


def _expected_replay_result(
    payload: object,
) -> MembershipCommandResult:
    if type(payload) is not dict:
        raise S015CommandReplayMismatch("completed receipt does not match request")
    try:
        return MembershipCommandResult(
            membership_uuid=UUID(payload["membership_uuid"]),
            resulting_state=str(payload["resulting_state"]),
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
        "membership_uuid": str(result.membership_uuid),
        "resulting_state": result.resulting_state,
        "state_known_at": canonical_timestamp(result.state_known_at),
        "state_source_event_set_fingerprint": result.state_source_event_set_fingerprint,
        "temporal_basis_kind": result.temporal_basis_kind,
        "temporal_basis_reference": result.temporal_basis_reference,
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
        resulting_state=transition.resulting_state,
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
            f"membership state {state} is unused by v1.0 (⟨B.049⟩)"
        )
    if state not in {"PROPOSED", "ACTIVE", "PROBATIONARY", "ENDED"}:
        raise S015MembershipEdgeRefused(f"membership edge {state} is not decided")
    return state


def _validate_edge(current_state: str, target_state: str, membership: OrganismMembership) -> None:
    if current_state in _STATE_UNUSED or target_state in _STATE_UNUSED:
        raise S015MembershipStateUnused(
            f"membership state {current_state}->{target_state} is unused by v1.0 (⟨B.049⟩)"
        )
    if current_state == "ENDED" and target_state == "PROPOSED":
        raise S015OperationNotYetImplemented("rejoin", "IL-1", "S4")
    if target_state == "ENDED" and current_state in {"ACTIVE", "PROBATIONARY"}:
        if membership.contextual_role_assignments.filter(state="ACTIVE").exists():
            raise S015OperationNotYetImplemented("leave_with_office", "IL-2", "S5")
    allowed = _STATE_ALLOWED.get(current_state)
    if allowed is None or target_state not in allowed:
        raise S015MembershipEdgeRefused(
            f"membership edge {current_state} -> {target_state} is refused"
        )


def _canonical_reason(value: object) -> str:
    return _canonical_key(value, "reason_class", 48)


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


class OrganismMembershipService(_OrganismCommandService):
    _action = _ACTION
    _not_found = S015MembershipNotFound
    _authority_unavailable = S015MembershipAuthorityUnavailable

    def _replay_result(self, payload: object) -> MembershipCommandResult:
        return _expected_replay_result(payload)

    def _command_fingerprint(
        self,
        *,
        actor: Identity,
        request_reference: str,
        idempotency_key: str,
        occurred_at: datetime,
        target: dict[str, object],
        command: dict[str, object],
    ) -> str:
        return _command_payload(
            domain=_COMMAND_DOMAIN,
            schema=_COMMAND_SCHEMA,
            action=_ACTION,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            target=target,
            command=command,
        )

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

        actor = _lock_actor(
            database_alias=self._alias,
            credential=command.credential,
            unavailable=S015MembershipAuthorityUnavailable,
        )
        acquire_organism_command_mutex(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            action=_ACTION,
            idempotency_key=idempotency_key,
        )

        try:
            organism = (
                LivingOrganism.objects.using(self._alias)
                .get(organism_id=living_organism_id)
            )
        except LivingOrganism.DoesNotExist as exc:
            raise S015MembershipNotFound("living organism not found") from exc

        target_fingerprint = _target_fingerprint_open(
            member_identity_id=member_identity_id,
            living_organism_id=living_organism_id,
            reason_class=reason_class,
        )
        command_fingerprint = self._command_fingerprint(
            actor=actor,
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
            viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
        )
        if replay is not None:
            return replay

        organism = (
            LivingOrganism.objects.using(self._alias)
            .select_for_update()
            .get(pk=organism.pk)
        )
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
            unavailable=S015MembershipAuthorityUnavailable,
        )

        decision = self._evaluate_authority(
            actor=actor,
            actor_membership=actor_membership,
            actor_role_refs=actor_role_refs,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            target_fingerprint=target_fingerprint,
            received_at=received_at,
        )

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
        departure_context: dict[str, str] = {}
        try:
            with authority_atomic(self._alias):
                return self._execute_transition(command, departure_context)
        except IntegrityError as exc:
            message_primary = getattr(
                getattr(exc.__cause__, "diag", None),
                "message_primary",
                None,
            )
            code = S015AuthorityRefusalCode.SBG_DEPARTURE_PRECEDES_PARTICIPATION
            if message_primary != code.value or command.resulting_state != "ENDED":
                raise
            effective_at = _validate_timestamp(command.effective_at, "effective_at")
            refusal = self._departure_precedes_participation_from_detail(
                getattr(
                    getattr(exc.__cause__, "diag", None),
                    "message_detail",
                    None,
                ),
                effective_at,
                person=departure_context.get("person"),
                organism_slug=departure_context.get("organism_slug"),
            )
            if refusal is None:
                raise
            raise refusal from exc

    def _departure_precedes_participation(
        self,
        membership: OrganismMembership,
        effective_at: datetime,
    ) -> S015MembershipRefused | None:
        participation = (
            CircleParticipation.objects.using(self._alias)
            .filter(
                membership=membership,
                effective_at__gt=effective_at,
            )
            .order_by("effective_at", "recorded_at", "pk")
            .first()
        )
        if participation is None:
            return None

        identity = membership.identity
        person = identity.display_name or identity.canonical_username
        organism = membership.living_organism
        return self._departure_precedes_participation_refusal(
            person=person,
            organism_slug=organism.slug,
            seat_id=participation.seat_id,
            departure_effective_at=canonical_timestamp(effective_at),
            participation_effective_at=canonical_timestamp(
                participation.effective_at
            ),
        )

    @staticmethod
    def _departure_precedes_participation_refusal(
        *,
        person: str,
        organism_slug: str,
        seat_id: str,
        departure_effective_at: str,
        participation_effective_at: str,
    ) -> S015MembershipRefused:
        message = (
            f"{person} was not recorded as leaving {organism_slug}, and nothing changed. "
            f"The leaving date given, {departure_effective_at}, is earlier than "
            f"the start of their GOVERNANCE seat {seat_id}, which began on "
            f"{participation_effective_at}. A member cannot leave "
            "before a seat they took while still a member. Check the leaving date. If the "
            "seat itself was recorded in error, it must be corrected through its own "
            "governed route first."
        )
        return S015MembershipRefused(message, refusal_code=(
            S015AuthorityRefusalCode.SBG_DEPARTURE_PRECEDES_PARTICIPATION
        ))

    @classmethod
    def _departure_precedes_participation_from_detail(
        cls,
        detail: object,
        effective_at: datetime,
        *,
        person: str | None,
        organism_slug: str | None,
    ) -> S015MembershipRefused | None:
        if type(detail) is not str:
            return None
        try:
            values = json.loads(detail)
        except json.JSONDecodeError:
            return None
        if type(values) is not dict or set(values) != {"seat_id", "started_at"}:
            return None
        seat_id = values.get("seat_id")
        participation_effective_at = values.get("started_at")
        if any(
            type(value) is not str or not value
            for value in (
                person,
                organism_slug,
                seat_id,
                participation_effective_at,
            )
        ):
            return None
        try:
            parsed_start = datetime.strptime(
                participation_effective_at,
                "%Y-%m-%dT%H:%M:%S.%fZ",
            )
        except ValueError:
            return None
        if parsed_start.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != participation_effective_at:
            return None
        return cls._departure_precedes_participation_refusal(
            person=person,
            organism_slug=organism_slug,
            seat_id=seat_id,
            departure_effective_at=canonical_timestamp(effective_at),
            participation_effective_at=participation_effective_at,
        )

    def _compose_current_participation_endings(
        self,
        *,
        membership: OrganismMembership,
        transition: OrganismMembershipTransition,
        effective_at: datetime,
        received_at: datetime,
    ) -> None:
        participations = list(
            CircleParticipation.objects.using(self._alias)
            .select_for_update(of=("self",))
            .filter(
                membership=membership,
                effective_at__lte=effective_at,
            )
            .order_by("pk")
        )
        if not participations:
            return

        existing_ending_ids = set(
            CircleParticipationEnd.objects.using(self._alias)
            .filter(participation_id__in=[item.pk for item in participations])
            .values_list("participation_id", flat=True)
        )
        composing_rule = GovernedRule.objects.using(self._alias).get(
            rule_uuid=_DEPARTURE_COMPOSITION_RULE_UUID,
            version=1,
            code="U3_MEMBERSHIP_END_ENDS_CIRCLE_PARTICIPATION",
        )
        composing_rule_reference = (
            f"rule:{composing_rule.rule_uuid}@{composing_rule.version}"
        )
        for participation in participations:
            if participation.pk in existing_ending_ids:
                continue
            CircleParticipationEnd.objects.using(self._alias).create(
                participation=participation,
                actor_state="NONE",
                actor=None,
                actor_access_epoch=None,
                actor_capacity="NA_NO_PARTY",
                entry_mode="COMPOSED",
                enterer_identity=membership.identity,
                composing_rule_state="REFERENCE",
                composing_rule=composing_rule,
                composing_rule_reference=composing_rule_reference,
                composer_state="REFERENCE",
                composer_identity=membership.identity,
                composed_from_event_uuid=transition.event_uuid,
                occurred_at=effective_at,
                effective_at=effective_at,
                received_at=received_at,
            )

    def _execute_transition(
        self,
        command: TransitionMembershipCommand,
        departure_context: dict[str, str],
    ) -> MembershipCommandResult:
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        reason_class = _canonical_reason(command.reason_class)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(command.received_at, "received_at")
        membership_uuid = _canonical_uuid(command.membership_uuid, "membership_uuid")
        target_state = _validate_member_state(command.resulting_state)

        actor = _lock_actor(
            database_alias=self._alias,
            credential=command.credential,
            unavailable=S015MembershipAuthorityUnavailable,
        )
        acquire_organism_command_mutex(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            action=_ACTION,
            idempotency_key=idempotency_key,
        )

        try:
            membership = (
                OrganismMembership.objects.using(self._alias)
                .select_related("identity", "living_organism")
                .get(membership_uuid=membership_uuid)
            )
        except OrganismMembership.DoesNotExist as exc:
            raise S015MembershipNotFound("membership not found") from exc

        target_fingerprint = _target_fingerprint_transition(
            membership_uuid=membership_uuid,
            living_organism_id=membership.living_organism.organism_id,
            resulting_state=target_state,
            reason_class=reason_class,
        )
        command_fingerprint = self._command_fingerprint(
            actor=actor,
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
            viewer=ReplayViewer(actor.pk, actor.identity_id, self._clock()),
        )
        if replay is not None:
            return replay

        _lock_registered_organism(
            database_alias=self._alias,
            organism_id=membership.living_organism.organism_id,
        )
        membership = (
            OrganismMembership.objects.using(self._alias)
            .select_for_update(of=("self",))
            .select_related("identity", "living_organism")
            .get(pk=membership.pk)
        )
        actor_membership, actor_role_refs = _lock_actor_membership_context(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            living_organism_id=membership.living_organism.organism_id,
            unavailable=S015MembershipAuthorityUnavailable,
            allow_probationary=(
                target_state == "ENDED" and membership.identity.identity_id == actor.identity_id
            ),
        )

        decision = self._evaluate_authority(
            actor=actor,
            actor_membership=actor_membership,
            actor_role_refs=actor_role_refs,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            target_fingerprint=target_fingerprint,
            received_at=received_at,
            effective_at=effective_at,
            target={
                "action": _ACTION.value,
                "living_organism_id": str(membership.living_organism.organism_id),
                "membership_uuid": str(membership_uuid),
                "reason_class": reason_class,
                "resulting_state": target_state,
                "schema": _TARGET_SCHEMA,
            },
            living_organism_id=membership.living_organism.organism_id,
        )

        current_state = _canonical_key(membership.state, "membership.state", 19)
        _validate_edge(current_state, target_state, membership)
        if (current_state, target_state) == ("PROPOSED", "ACTIVE"):
            raise S015OperationNotYetImplemented("membership_admission", "S1-IL-ADMISSION", "S3")
        if (current_state, target_state) in {("PROPOSED", "PROBATIONARY"), ("PROBATIONARY", "ACTIVE")}:
            raise S015OperationNotYetImplemented("membership_probation", "S3-IL-PROBATION", "S5")

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

            if target_state == "ENDED":
                identity = membership.identity
                departure_context["person"] = (
                    identity.display_name or identity.canonical_username
                )
                departure_context["organism_slug"] = membership.living_organism.slug
                refusal = self._departure_precedes_participation(
                    membership,
                    effective_at,
                )
                if refusal is not None:
                    raise refusal

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

        if target_state == "ENDED":
            self._compose_current_participation_endings(
                membership=membership,
                transition=transition,
                effective_at=effective_at,
                received_at=received_at,
            )

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