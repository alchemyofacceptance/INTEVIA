"""Membership commands and own-membership reads for a Living Organism (S015).

OrganismMembershipService provides open_membership and
transition_membership. The opening path creates a PROPOSED membership and
its first transition inside transaction.atomic. The transition path runs
inside authority_atomic and applies authority qualification, edge checks
and the implemented limits; _STATE_ALLOWED does not by itself establish
that an edge is delivered.

A successful fresh command writes a membership transition, refreshes the
governed cache and records a command receipt. Both paths attempt replay
before their fresh-command work and return a replay result when supplied
by _attempt_replay.

For a fresh transition reaching the departure checks, a participation
starting after the departure's effective_at causes a refusal, whether or
not that participation already has an ending. After writing an ENDED
transition, the service composes endings at that same effective_at for
participations starting by departure that have no existing ending row
(PKT-B SB-G build commission, section 8).

Local checks retain named limits for admission, probation transitions,
rejoining, leaving with an ACTIVE role assignment, and a transition whose
(effective_at, occurred_at, received_at) tuple precedes the head's.
Authority qualification or another earlier check can refuse first.

OrganismMembershipReader filters the reader's own cached memberships to
ACTIVE, PROBATIONARY, RESTRICTED or SUSPENDED, with state-effective and
state-known timestamps no later than its reading time.
qualify_completed_replay requires an atomic block and checks a command
receipt against the supplied request and authority references.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
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
    S015CommandReceiptMalformed,
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
    contention_translated,
)

if TYPE_CHECKING:
    from src.intevia.services.command_intake import BoundRequest


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
    """Base class for this module's membership-command errors."""
    pass


class S015MembershipAuthorityUnavailable(S015MembershipCommandError):
    """An authority prerequisite for a membership command is unavailable.

    Raised when the actor's Identity, credential or membership context fails
    its check (passed as the unavailable exception to _lock_actor and
    _lock_actor_membership_context), or when the LO has no founding
    authority basis.
    """
    pass


class S015MembershipExists(S015MembershipCommandError):
    """Opening refused because a membership row already exists for that Identity and LO."""
    pass


class S015MembershipNotFound(S015MembershipCommandError):
    """The LO, target Identity or membership named by a command or read was not found.

    Also used by OrganismMembershipReader when a membership is absent,
    belongs to another Identity, or is outside its filters.
    """
    pass


class S015MembershipEdgeRefused(S015MembershipCommandError):
    """A requested state or membership edge is outside the decided set."""
    pass


class S015MembershipRefused(S015MembershipCommandError):
    """An authored refusal of a membership command, carrying its refusal_code.

    transition_membership raises it for a departure dated before one of the
    member's participations began (SBG_DEPARTURE_PRECEDES_PARTICIPATION).
    """
    def __init__(self, message: str, *, refusal_code: S015AuthorityRefusalCode) -> None:
        """Set the message and refusal_code."""
        super().__init__(message)
        self.refusal_code = refusal_code


class S015MembershipStateUnused(S015MembershipCommandError):
    """A membership state that v1.0 does not use was requested or found."""
    pass


class S015OperationNotYetImplemented(S015MembershipCommandError):
    """A named limit: an operation the design supports but this build refuses.

    Carries operation, limit_id and owning_slice, and the message
    "<operation> is supported by the design but not yet built (<limit_id>,
    <owning_slice>)".
    """
    def __init__(self, operation: str, limit_id: str, owning_slice: str):
        """Compose the message from operation, limit_id and owning_slice, and
        keep each as an attribute."""
        super().__init__(
            f"{operation} is supported by the design but not yet built ({limit_id}, {owning_slice})"
        )
        self.operation = operation
        self.limit_id = limit_id
        self.owning_slice = owning_slice


@dataclass(frozen=True, slots=True)
class OpenMembershipCommand:
    """Inputs to open_membership.

    The actor's credential; the request reference and idempotency key; the
    occurred and effective times; the LO and member Identity UUIDs; and the
    reason class. INTEVIA assigns received_at; a supplied value is refused.
    """
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime | None = field(default=None, kw_only=True)
    living_organism_id: UUID
    member_identity_id: UUID
    reason_class: str


@dataclass(frozen=True, slots=True)
class TransitionMembershipCommand:
    """Inputs to transition_membership.

    The actor's credential; the request reference and idempotency key; the
    occurred and effective times; the membership UUID; the requested resulting
    state; and the reason class. INTEVIA assigns received_at; a supplied value
    is refused.
    """
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime | None = field(default=None, kw_only=True)
    membership_uuid: UUID
    resulting_state: str
    reason_class: str


@dataclass(frozen=True, slots=True)
class MembershipCommandResult:
    """A membership command result, with fields that may be withheld when served.

    The receipt stores the serialized result before serve_replay applies
    viewer-specific withholding. The shared service applies serve_replay
    both when returning a newly completed command and on later replay.

    resulting_state, transition_sequence, current_state_effective_at,
    state_known_at and state_source_event_set_fingerprint are replaced by
    WithheldField when the viewer lacks the SUBJECT audience.
    membership_uuid has the same restriction when transition_sequence is 1.

    Separately, authority_reference is replaced by WithheldField whenever
    _has_pk_valued_reference identifies it as primary-key-valued, regardless
    of the viewer's audience. These rules do not replace lineage_reference,
    temporal_basis_kind, temporal_basis_reference or
    authority_decision_reference.
    """
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
        """Return the receipt payload for this result (_result_payload)."""
        return _result_payload(self)

    @classmethod
    def replay_field_audiences(cls, result: "MembershipCommandResult") -> dict[str, str]:
        """Return the replay audience of each restricted field.

        resulting_state, transition_sequence, current_state_effective_at,
        state_known_at and state_source_event_set_fingerprint are for the
        membership's own Identity (SUBJECT) only; membership_uuid also, when the
        result is a membership's first transition (sequence 1).
        """
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
    """One of a person's own memberships, as returned by OrganismMembershipReader.

    organism_id identifies the LO. state is the membership's stored state.
    current_state_effective_at is when that state began (field label "the
    beginning of the current state"). offices lists (role code, scope) pairs
    for the membership's ACTIVE role assignments in effect at the reading time.
    """
    organism_id: UUID
    state: str
    current_state_effective_at: datetime = field(
        metadata={"label": "the beginning of the current state"},
    )
    offices: tuple[tuple[str, str], ...]


class OrganismMembershipReader:
    """Read memberships belonging to the supplied Identity.

    The class's own queries perform reads without requesting row locks.
    Membership selection uses cached rows in ACTIVE, PROBATIONARY,
    RESTRICTED or SUSPENDED whose current_state_effective_at and
    state_known_at are no later than the reading time. It does not
    reconstruct membership state by folding membership events.

    Office eligibility is delegated to assignment_in_effect; this
    description makes no claim about that helper's internal queries.
    """
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        database_alias: str = "default",
    ) -> None:
        """Configure the reader's clock and database alias.

        If clock is given it must be callable (TypeError otherwise) and supplies
        the reading time; without it, the reading time is the database's
        clock_timestamp(). database_alias must be a non-empty str (ValueError
        otherwise); it defaults to "default".
        """
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        if type(database_alias) is not str or not database_alias:
            raise ValueError("database_alias is required")
        self._clock = clock
        self._alias = database_alias

    def list_own_memberships(self, reader: Identity) -> list[OwnMembershipView]:
        """Return the reader's own memberships visible at one reading time.

        Takes the reading time once, then selects memberships for the reader's
        Identity UUID in the states ACTIVE, PROBATIONARY, RESTRICTED and
        SUSPENDED whose current_state_effective_at and state_known_at are no
        later than it, ordered by LO UUID, and builds a view of each.
        """
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
        """Return one of the supplied Identity's memberships by its UUID.

        Validates that membership_uuid has the required UUID type, then applies
        the same membership filters as list_own_memberships at one reading time.
        Raises S015MembershipNotFound("membership not found") when no row
        matches, whether the membership is absent, belongs to another Identity,
        or falls outside the permitted states or timestamp bounds.

        Invalid reader or UUID arguments are rejected before this lookup.
        """
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
        """Return reader.identity_id after checking its types.

        Raises TypeError unless reader is an instance of Identity and
        type(reader.identity_id) is UUID. This does not authenticate the caller
        or check the Identity's current access state.
        """
        if not isinstance(reader, Identity) or type(reader.identity_id) is not UUID:
            raise TypeError("reader must be an Identity")
        return reader.identity_id

    def _now(self) -> datetime:
        """Return the reading time.

        The configured clock's value, validated by _validate_timestamp, or else
        the database's clock_timestamp() on this reader's alias.
        """
        if self._clock is not None:
            return _validate_timestamp(self._clock(), "clock")
        with connections[self._alias].cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            return cursor.fetchone()[0]

    def _view(self, membership: OrganismMembership, now: datetime) -> OwnMembershipView:
        """Build the OwnMembershipView for one membership at the reading time.

        offices keeps the (code, scope) of each ACTIVE role assignment on this
        membership, ordered by role code, scope and primary key, for which
        assignment_in_effect (organism_role_service) reports the assignment in
        effect at that time.
        """
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
    actor_id: int | None,
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
    actor_access_epoch: int | None,
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
    """Execute a raw SQL insert of one membership transition and return its id.

    Reads the predecessor's l1_commitment when predecessor_id is supplied;
    uses None when no predecessor is supplied or the lookup finds no row.
    Computes parts_commitment with the database's s015_0022_digest and
    s015_0022_parts_preimage functions for this table and event_uuid.

    Passes the supplied row values and the two derived commitments to a
    parameterized INSERT ... RETURNING id on database_alias. Performs no
    explicit Python-side validation of those values and does not open an
    atomic block or explicitly lock the predecessor row.

    Database errors propagate. Returning an id does not establish that
    deferred checks have passed or that the surrounding transaction commits.
    """
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
    """Parse a timestamp value from a stored receipt payload.

    Requires type(value) to be str, otherwise raises TypeError. Replaces
    every "Z" with "+00:00" and passes the result to datetime.fromisoformat,
    allowing its parsing errors to propagate. Raises ValueError unless
    canonical_timestamp of the parsed value reproduces the stored string
    exactly, so only the writer's canonical spelling is accepted.
    """
    if type(value) is not str:
        raise TypeError("timestamp payload must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if canonical_timestamp(parsed) != value:
        raise ValueError("timestamp payload is not canonical")
    return parsed


def _expected_replay_result(
    payload: object,
) -> MembershipCommandResult:
    """Reconstruct a MembershipCommandResult from a receipt payload, strictly.

    Enforces the writer's receipt contract: the payload must be a dict with
    exactly the eleven result keys; the reference, state and fingerprint
    fields must be strings, the temporal basis kind and reference strings or
    None and present or absent together, and transition_sequence a positive
    int (bool refused). membership_uuid must be a canonical UUID string;
    resulting_state must be PROPOSED, ACTIVE, PROBATIONARY or ENDED; the
    temporal basis kind must be None, RETROSPECTIVE or PROSPECTIVE; the two
    timestamps must be canonical (_parse_canonical_timestamp).

    A non-dict payload, or KeyError, TypeError or ValueError while checking
    or building the result, raises S015CommandReceiptMalformed (the record-
    fault refusal) with the cause chained. It does not compare the result
    with the recorded transition; the service's _verify_replay_result does.
    """
    if type(payload) is not dict:
        raise S015CommandReceiptMalformed() from TypeError("receipt payload must be a dict")
    try:
        # RC-1 commission v1.3, item 1: enforce the writer's receipt contract.
        expected_keys = {
            "authority_decision_reference",
            "authority_reference",
            "current_state_effective_at",
            "lineage_reference",
            "membership_uuid",
            "resulting_state",
            "state_known_at",
            "state_source_event_set_fingerprint",
            "temporal_basis_kind",
            "temporal_basis_reference",
            "transition_sequence",
        }
        if set(payload) != expected_keys:
            raise ValueError("receipt fields do not match the membership result contract")
        for key in (
            "authority_decision_reference",
            "authority_reference",
            "current_state_effective_at",
            "lineage_reference",
            "membership_uuid",
            "resulting_state",
            "state_known_at",
            "state_source_event_set_fingerprint",
        ):
            if type(payload[key]) is not str:
                raise TypeError(f"{key} must be a string")
        for key in ("temporal_basis_kind", "temporal_basis_reference"):
            if payload[key] is not None and type(payload[key]) is not str:
                raise TypeError(f"{key} must be a string or None")
        if type(payload["transition_sequence"]) is not int or payload["transition_sequence"] < 1:
            raise TypeError("transition_sequence must be a positive integer")
        membership_uuid = UUID(payload["membership_uuid"])
        if str(membership_uuid) != payload["membership_uuid"]:
            raise ValueError("membership_uuid must use a canonical string")
        if payload["resulting_state"] not in {"PROPOSED", "ACTIVE", "PROBATIONARY", "ENDED"}:
            raise ValueError("resulting_state is outside the membership state vocabulary")
        if payload["temporal_basis_kind"] not in {None, "RETROSPECTIVE", "PROSPECTIVE"}:
            raise ValueError("temporal_basis_kind is outside the result vocabulary")
        if (payload["temporal_basis_kind"] is None) != (payload["temporal_basis_reference"] is None):
            raise ValueError("temporal basis reference does not match its kind")
        return MembershipCommandResult(
            membership_uuid=membership_uuid,
            resulting_state=payload["resulting_state"],
            transition_sequence=payload["transition_sequence"],
            lineage_reference=payload["lineage_reference"],
            temporal_basis_kind=payload["temporal_basis_kind"],
            temporal_basis_reference=payload["temporal_basis_reference"],
            current_state_effective_at=_parse_canonical_timestamp(payload["current_state_effective_at"]),
            state_known_at=_parse_canonical_timestamp(payload["state_known_at"]),
            state_source_event_set_fingerprint=payload["state_source_event_set_fingerprint"],
            authority_reference=payload["authority_reference"],
            authority_decision_reference=payload["authority_decision_reference"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise S015CommandReceiptMalformed() from exc


def _result_payload(result: MembershipCommandResult) -> dict[str, object]:
    """Return the receipt payload for a result.

    Its eleven fields, keyed by name, with membership_uuid as a string and
    current_state_effective_at and state_known_at in canonical form.
    """
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
    """Build the command result after a write.

    resulting_state, transition_sequence, lineage_reference and the temporal
    basis come from the transition row; membership_uuid and the state
    coordinates (current_state_effective_at, state_known_at and the source
    event-set fingerprint) from the refreshed membership; the two authority
    references are those supplied.
    """
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
    """Validate a requested resulting state and return it.

    Validates it as a canonical key of at most 19 characters. Raises
    S015MembershipStateUnused for SUSPENDED, RESTRICTED or
    PRIVACY_TRANSFORMED (unused in v1.0), and S015MembershipEdgeRefused for
    any other state that is not PROPOSED, ACTIVE, PROBATIONARY or ENDED.
    """
    state = _canonical_key(value, "resulting_state", 19)
    if state in _STATE_UNUSED:
        raise S015MembershipStateUnused(
            f"membership state {state} is unused by v1.0 (⟨B.049⟩)"
        )
    if state not in {"PROPOSED", "ACTIVE", "PROBATIONARY", "ENDED"}:
        raise S015MembershipEdgeRefused(f"membership edge {state} is not decided")
    return state


def _validate_edge(current_state: str, target_state: str, membership: OrganismMembership) -> None:
    """Check one membership edge against the v1.0 edge rules; raise or return None.

    In order: S015MembershipStateUnused if either state is unused in v1.0;
    S015OperationNotYetImplemented for ENDED to PROPOSED (rejoining, limit
    IL-1); S015OperationNotYetImplemented for an ENDED target from ACTIVE or
    PROBATIONARY while the membership has a contextual role assignment
    whose stored state is ACTIVE (leaving while holding an office, limit
    IL-2); S015MembershipEdgeRefused for any other edge not in
    _STATE_ALLOWED. The office check reads stored assignment states; it does
    not fold assignment events at an effective time.
    """
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
    """Validate reason_class as a canonical key of at most 48 characters and return it."""
    return _canonical_key(value, "reason_class", 48)


def _target_fingerprint_open(
    *,
    member_identity_id: UUID,
    living_organism_id: UUID,
    reason_class: str,
) -> str:
    """Return the authority target fingerprint for opening a membership.

    Passes authority_target_fingerprint the action (TRANSITION_MEMBERSHIP),
    the LO and member Identity UUIDs as strings, the reason class, the
    resulting state PROPOSED and the target schema.
    """
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
    """Return the authority target fingerprint for a membership transition.

    Passes authority_target_fingerprint the action (TRANSITION_MEMBERSHIP),
    the LO and membership UUIDs as strings, the reason class, the requested
    resulting state and the target schema.
    """
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
    """The membership command service: open_membership and transition_membership.

    Declares, for the shared _OrganismCommandService, the action
    TRANSITION_MEMBERSHIP, S015MembershipNotFound as its not-found exception
    and S015MembershipAuthorityUnavailable as its unavailable exception;
    rebuilds replayed results from receipt payloads and, on replay, checks
    each against its recorded membership transition.
    """
    _action = _ACTION
    _not_found = S015MembershipNotFound
    _authority_unavailable = S015MembershipAuthorityUnavailable

    def _replay_result(self, payload: object) -> MembershipCommandResult:
        """Rebuild a replayed result from its receipt payload (_expected_replay_result)."""
        return _expected_replay_result(payload)

    def _verify_replay_result(self, result: MembershipCommandResult) -> None:
        """Check a replayed membership result against its recorded transition.

        Raises TypeError unless result is a MembershipCommandResult instance.
        Reads the transition identified by the result's membership UUID and
        transition sequence. Requires its action to equal _ACTION.value
        (OrganismAction.TRANSITION_MEMBERSHIP) and its lineage reference to
        equal the result's. A missing row or either disagreement raises
        S015CommandReceiptMalformed with a ValueError cause (RC-1, item 7).

        This check does not compare the other result fields with the transition
        or recompute its lineage reference. Database-read errors propagate.
        """
        if not isinstance(result, MembershipCommandResult):
            raise TypeError("membership replay result has the wrong type")
        # RC-1 commission v1.3, item 7 (A2-b): agree with the chain only on replay.
        transition = (
            OrganismMembershipTransition.objects.using(self._alias)
            .filter(
                membership__membership_uuid=result.membership_uuid,
                sequence=result.transition_sequence,
            )
            .values_list("action", "lineage_reference")
            .first()
        )
        if transition is None or transition != (_ACTION.value, result.lineage_reference):
            raise S015CommandReceiptMalformed() from ValueError(
                "receipt does not agree with its membership chain transition"
            )

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
        """Return the command fingerprint for this service's commands.

        Delegates to _command_payload with this module's command domain and
        schema, the TRANSITION_MEMBERSHIP action, the actor's Identity UUID and
        access epoch, and the caller's request reference, idempotency key,
        occurred_at, target and command. Opening and transitions both use the
        TRANSITION_MEMBERSHIP action (the interim S1 mapping at l.63-64).
        """
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
        """Open a PROPOSED membership; runs _execute_open in transaction.atomic.

        Uses this service's database alias. The transaction runs inside
        contention_translated: an OperationalError with SQLSTATE 40P01, 40001
        or 55P03 becomes S015CommandContended when this is the outermost
        transaction. Those errors inside an enclosing transaction, and every
        other exception raised by _execute_open, propagate unchanged; no
        IntegrityError is mapped to an authored refusal.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
            intake_shape,
        )

        entry = IntakeEntry.OPEN_MEMBERSHIP
        _require_admission_state(self._alias)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("OPEN_MEMBERSHIP is not active")
        shape = intake_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_open_membership(command, bound)

    def _execute_open_membership(
        self, command: OpenMembershipCommand, bound: BoundRequest,
    ) -> MembershipCommandResult:
        """Execute an admitted opening using the existing contention and transaction form."""
        # Keep T-LOCK-1 translation outside atomic so it follows rollback (15 Oct commission, §1).
        with contention_translated(self._alias):
            with transaction.atomic(using=self._alias):
                return self._execute_open(command, bound=bound)

    def _execute_open(
        self, command: OpenMembershipCommand, *, bound: BoundRequest,
    ) -> MembershipCommandResult:
        """Open a membership in PROPOSED; called by open_membership.

        Runs inside the caller's transaction.atomic block, in this order:

        1. Validates the canonical keys, reason, LO UUID and member Identity UUID,
           and validates and normalises the timestamps; locks the actor's
           Identity and credential (_lock_actor) and takes the command mutex.
        2. Reads the LO without requesting a row lock (S015MembershipNotFound
           if absent) and computes the target and command fingerprints. If
           _attempt_replay returns a result, returns it with no further work.
        3. Locks the LO row itself, then reads the target Identity without
           requesting an additional row lock (S015MembershipNotFound if absent),
           then locks the actor's membership context. The context helper
           requires the actor's membership in this LO to be ACTIVE and to have
           a non-null head transition; allow_probationary is not supplied.
        4. Calls _evaluate_authority with the actor, its membership context
           and role references, the request reference and idempotency key,
           the target fingerprint and received_at. It does not supply
           effective_at, a target dictionary or living_organism_id; those
           request fields default to None. The following steps are reached
           only if authority qualification returns successfully.
        5. If the LO's founding authority basis id is null, raises
           S015MembershipAuthorityUnavailable; otherwise locks the basis row.
        6. Raises S015MembershipExists if a membership row for this Identity and
           LO already exists, in any state.
        7. Creates the membership in PROPOSED, its state effective at the
           command's effective_at and known at its received_at, and writes its
           first transition: sequence 1, no predecessor and no prior state, the
           same Identity as actor and enterer, entry_mode TRANSPORT, the
           decision's references, and a lineage_reference hashed from the
           constructed lineage payload.
        8. Re-reads the membership, refreshes the governed cache at
           effective_at with the database's clock_timestamp(), then
           re-reads the membership and fetches and refreshes the transition.
        9. Builds the result and returns it through _finalise.

        The LO row is locked directly; this path does not call
        _lock_registered_organism. R1 (PKT_LOCK_1_BUILD_COMMISSION_v1_0.md,
        section 2.4) removes the explicit target Identity lock: this path does
        not check the target's access state and uses the row for the membership
        pair lookup and foreign key. This also satisfies I-2's prohibition on
        explicit locks on another person's Identity after the organism lock.
        It does not establish the target's eligibility or resolve H25's
        target-deactivation question.

        The write sequence described here is conditional on successful
        authority qualification. The supplied production application-authority
        provider refuses TRANSITION_MEMBERSHIP requests without an ENDED
        target; this opening path supplies no target.
        """
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        reason_class = _canonical_reason(command.reason_class)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(bound.received_at, "received_at")
        living_organism_id = _canonical_uuid(command.living_organism_id, "living_organism_id")
        member_identity_id = _canonical_uuid(command.member_identity_id, "member_identity_id")

        actor = _lock_actor(
            database_alias=self._alias,
            credential=command.credential,
            unavailable=S015MembershipAuthorityUnavailable,
        )
        from src.intevia.services.command_intake import IntakeEntry

        bound.verify(command, actor, IntakeEntry.OPEN_MEMBERSHIP, self._alias)
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
        """Run the membership transition inside an authority_atomic block.

        Delegate to _execute_transition. If an IntegrityError escapes that block,
        map it only when its cause's diagnostic primary message is exactly
        SBG_DEPARTURE_PRECEDES_PARTICIPATION and command.resulting_state is ENDED.
        Reconstruct the authored S015MembershipRefused from the validated command
        effective_at, the diagnostic detail, and the person and organism slug
        captured in memory by _execute_transition; raise it from the original error.

        Re-raise other IntegrityErrors, or the matching error when reconstruction
        returns None. This handler does not catch a deferred constraint failure
        raised later when a caller's enclosing transaction commits.
        """
        from src.intevia.services.command_intake import (
            ACTIVE_ENTRIES,
            IntakeAdmissionTechnicalFailure,
            IntakeEntry,
            _require_admission_state,
            _resolve_actor,
            admit_request,
            intake_shape,
        )

        entry = IntakeEntry.TRANSITION_MEMBERSHIP
        _require_admission_state(self._alias)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("TRANSITION_MEMBERSHIP is not active")
        shape = intake_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_transition_membership(command, bound)

    def _execute_transition_membership(
        self, command: TransitionMembershipCommand, bound: BoundRequest,
    ) -> MembershipCommandResult:
        """Execute an admitted transition using the existing refusal translation."""
        departure_context: dict[str, str] = {}
        try:
            with authority_atomic(self._alias):
                return self._execute_transition(
                    command, departure_context, bound=bound
                )
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
        """Return the authored refusal for a departure dated before a seat began, or None.

        Finds the membership's earliest participation (by effective_at, recorded_at,
        pk) whose effective_at is after the departure's effective_at, regardless of
        any ending, and returns the SBG_DEPARTURE_PRECEDES_PARTICIPATION refusal for
        it. Returns None when there is none.
        """
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
        """Build the SBG_DEPARTURE_PRECEDES_PARTICIPATION refusal.

        The message names the person, the LO's slug, the departure date, the seat
        and the date it began, states that nothing changed, and directs the reader
        to check the leaving date or correct the seat through its own governed route.
        """
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
        """Rebuild the refusal from the completeness guard's JSON detail, or None.

        Requires a JSON object with exactly the keys seat_id and started_at, all of
        seat_id, started_at, person and organism_slug non-empty strings, and
        started_at in the exact form YYYY-MM-DDTHH:MM:SS.ffffffZ (it must round-trip
        unchanged). Returns None when any requirement fails.
        """
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
        """Compose departure endings for eligible, not-already-ended participations.

        Within the caller's transaction, select and lock this membership's
        participations with effective_at at or before the supplied effective_at,
        ordered by primary key and using select_for_update(of=("self",)).
        The query applies no recorded_at cutoff. Return if it selects no rows.

        Read the selected participations' existing endings, without an ending-date
        filter. Fetch the U-3 composition rule by its fixed UUID, version 1 and code,
        then skip every participation with an existing ending.

        For each remaining participation, create a CircleParticipationEnd with
        actor NONE, null actor and access epoch, capacity NA_NO_PARTY, entry mode
        COMPOSED, and REFERENCE states for the rule and composer. Use the member's
        identity as enterer and composer, the fetched rule and its reference, and
        transition.event_uuid as the causal link. Set effective_at and occurred_at
        to the supplied effective_at, and received_at to the supplied received_at;
        recorded_at is assigned by the database.

        Create no separate authority decision. This helper neither selects nor
        refuses participations starting after effective_at; the caller's earlier
        check and the database completeness guard address that condition.
        """
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
        *,
        bound: BoundRequest,
    ) -> MembershipCommandResult:
        """Carry out one membership transition; called by transition_membership.

        Runs inside the caller's authority_atomic transaction, in this order:

        1. Validates the canonical keys, reason and membership UUID, validates
           and normalises the timestamps, and validates the requested state;
           locks the actor's Identity row and takes the command mutex.
        2. Reads the membership (S015MembershipNotFound if absent) and computes the
           target and command fingerprints. If _attempt_replay returns a result,
           returns it with no further work.
        3. Takes the registered organism lock, then locks the membership row
           itself, without locking its joined Identity or LivingOrganism rows
           through that query. Locks the actor's membership context, which
           requires ACTIVE membership, or ACTIVE or PROBATIONARY when the actor
           is ending their own membership, and a non-null head transition.
        4. Calls _evaluate_authority before _validate_edge and the subsequent
           local named-limit checks. An authority refusal stops this path before
           those checks; successful qualification does not bypass them.
        5. Validates the edge (_validate_edge), then refuses PROPOSED to ACTIVE,
           PROPOSED to PROBATIONARY and PROBATIONARY to ACTIVE as named limits
           (S015OperationNotYetImplemented). The edges this method completes are
           therefore those to ENDED.
        6. If the membership has no head transition, raises
           S015MembershipNotFound ("membership head transition not found")
           before reading any predecessor. Otherwise refuses, as a named limit
           (IL-3), a command whose (effective_at, occurred_at, received_at) sorts
           strictly before the head's; for an ENDED target, records the member's
           display name (or username) and the LO's slug in departure_context and
           raises the refusal from _departure_precedes_participation, if any; and
           reads the id of the LO's founding authority basis.
        7. If the assigned founding basis id is null, raises
           S015MembershipAuthorityUnavailable. Otherwise locks the basis row
           and writes the transition with the predecessor's sequence plus one,
           the same Identity as actor and enterer, entry_mode TRANSPORT, the
           decision's references, and a lineage_reference hashed from the
           constructed lineage payload. Refreshes the governed cache at the
           transition's effective_at with the database's clock_timestamp(),
           then re-reads the membership and transition.
        8. For an ENDED target, composes the participation endings
           (_compose_current_participation_endings).
        9. Builds the result and returns it through _finalise.

        A membership whose head_transition_id is None is refused at step 6 as
        not found, before this method reads that predecessor or inserts its
        transition. Earlier authority, edge and named-limit checks may refuse
        first. This branch does not itself establish why the head is missing.
        """
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        reason_class = _canonical_reason(command.reason_class)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(bound.received_at, "received_at")
        membership_uuid = _canonical_uuid(command.membership_uuid, "membership_uuid")
        target_state = _validate_member_state(command.resulting_state)

        actor = _lock_actor(
            database_alias=self._alias,
            credential=command.credential,
            unavailable=S015MembershipAuthorityUnavailable,
        )
        from src.intevia.services.command_intake import IntakeEntry

        bound.verify(command, actor, IntakeEntry.TRANSITION_MEMBERSHIP, self._alias)
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

        if membership.head_transition_id is None:
            # RC-1 commission v1.3, item 3: refuse before reading the predecessor.
            raise S015MembershipNotFound("membership head transition not found")
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
    """A completed command receipt that matched its request.

    Returned by qualify_completed_replay: the receipt's primary key, actor
    Identity UUID, action, idempotency key, request reference, payload
    fingerprint, authority decision reference, lineage reference and stored
    result payload, with the database alias it was read from.
    """
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
    """Look up a command receipt and compare three request-binding fields.

    Requires an atomic block on database_alias, otherwise raises
    S015CommandAtomicityRequired. Reads the actor Identity without requesting
    a row lock and returns None if absent. Selects the receipt for that actor,
    action and idempotency key for update, returning None if absent.

    Only after finding a receipt, validates request_reference as a canonical
    key of at most 128 characters. Compares that reference, payload_fingerprint
    and authority_decision_reference with the stored values. A difference
    raises S015CommandReplayMismatch; a match returns
    QualifiedOrganismCommandReplay with the receipt data.

    Does not authenticate the caller, check the actor's access state or
    epoch, resolve the authority decision reference, or reconstruct and
    validate result_payload.
    """
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
    "S015CommandReceiptMalformed",
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