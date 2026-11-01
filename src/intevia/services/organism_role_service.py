"""Office proposals, appointments and authorised endings for an LO (S015).

OrganismRoleService provides propose_assignment and
transition_assignment, each admitted through the shared intake and then
run inside authority_atomic, plus assignment_in_effect and
open_nominations reads.

A successful proposal creates a contextual role assignment in PROPOSED
state for a membership whose stored state is ACTIVE, with a sequence-1
PROPOSE event. It uses an existing role definition. The transition
service supports PROPOSED to ACTIVE by ASSIGN and ACTIVE to ENDED by END
or REASSIGN. A fresh appointment uses a new assignment anchor.

ASSIGN requires the nominee's stored membership state to be ACTIVE and
refuses a nomination with a membership departure recorded after its
proposal. It also checks that the authority reference names the consent
fact selected for that nomination. For self-nomination this is the
proposal event unless an eligible withdrawal exists; otherwise it is
the highest-ordinal eligible nominee record, which must be CONSENT.
Consent eligibility uses the appointment's effective_at and the
database time sampled by the consent check.

ASSIGN supplies the term; END and REASSIGN reject supplied term fields.
The term and default no-tolling value are bound into ASSIGN's
fingerprints. The term is written on the event; the no-tolling value
and its source are included in the result and receipt, not separate
event columns. A proposed term is fingerprinted but the PROPOSE event
and result establish no term.

assignment_in_effect combines the cached assignment state with the
recorded FIXED-term cutoff, returning False at and after effective_until.
This module exposes no command that automatically records term expiry.
open_nominations folds assignment state at supplied effective and
knowledge coordinates and excludes nominations with an eligible
DECLINE or WITHDRAW record.

Commands validate inputs, lock the actor, acquire the command mutex,
resolve the target and attempt replay. Fresh commands then obtain the
required actor context and locks and qualify authority before their
local target-state refusals. Actor-context checks can refuse earlier.
Successful commands write an event, refresh the governed cache and
finalise a receipt. The intake record commits separately from the
command: when the command's transaction rolls back, its writes roll back
and the intake record remains. A provider denial can leave its REFUSED
authority decision, and the founder-protection denial that
transition_assignment raises after successful qualification can leave
that QUALIFIED decision; authority_atomic does not itself isolate
refusal evidence from other writes.

Local implementation limits cover non-LO-scoped definitions,
non-COORDINATOR codes, role suspension and events whose
(effective_at, occurred_at, received_at) precedes the head's.
A non-founder cannot END or REASSIGN an assignment recognised as the
founder's original appointment while no Birthing event is eligible
at effective_at and received_at (C_FOUNDER_PROTECTED).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import connections, transaction

from core.models import (
    AuthorityBasis,
    ContextualRoleAssignment,
    ContextualRoleAssignmentStateEvent,
    Identity,
    LivingOrganism,
    OfficeAppointmentConsent,
    OrganismMembership,
    OrganismMembershipTransition,
    OrganismRoleDefinition,
)
from core.organism_contract import OrganismAction
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
    _founding_provenance_assignment_id,
    _intake_tail,
    _lock_actor,
    _lock_actor_membership_context,
    _lock_registered_organism,
    _read_actor_active_membership,
    _OrganismCommandService,
    _resulting_temporal_basis,
    _validate_timestamp,
    WithheldField,
    authority_atomic,
    acquire_organism_command_mutex,
    authority_target_fingerprint,
    canonical_json_bytes,
    canonical_timestamp,
    command_mutex_key,
)
from src.intevia.services.organism_membership_service import (
    S015OperationNotYetImplemented,
)
from src.intevia.services.organism_authority import (
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
)

if TYPE_CHECKING:
    from src.intevia.services.command_intake import BoundRequest


_COMMAND_DOMAIN = b"INTEVIA:S015:ORGANISM_ROLE_COMMAND:v1\x00"
_TARGET_SCHEMA = "intevia.s015.organism-role-target.v1"
_COMMAND_SCHEMA = "intevia.s015.organism-role-command.v1"
_LINEAGE_SCHEMA = "intevia.s015.organism-role-lineage.v1"
# The ordinary-plane subtype row from the landed contract (core/organism_contract.py); S2 adds none.
_ACTION = OrganismAction.TRANSITION_CONTEXTUAL_ROLE
_ANCHOR_TOKEN = "core_contextualroleassignment"
_COORDINATOR_ROLE_CODE = "COORDINATOR"
_OPEN_STATE = "PROPOSED"
# The design default: no tolling (⟨C.064⟩, ⟨E2.130⟩). No settings store is landed (HD-E6 open),
# so the value in force at the appointment's effective_at is the default, recorded explicitly.
_TOLLING_VALUE = "NO"
_TOLLING_SOURCE = "DEFAULT (no settings store)"
_STATE_VOCABULARY = frozenset({"PROPOSED", "ACTIVE", "PROBATIONARY", "SUSPENDED", "ENDED", "PRIVACY_TRANSFORMED"})
_STATE_UNUSED = frozenset({"PRIVACY_TRANSFORMED"})
_ACTION_VOCABULARY = frozenset({"PROPOSE", "ASSIGN", "END", "REASSIGN", "SUSPEND", "PRIVACY_TRANSFORM"})
_ACTION_UNUSED = frozenset({"PRIVACY_TRANSFORM"})
# The decided set (⟨B.050⟩): PROPOSED -> ACTIVE by ASSIGN; ACTIVE -> ENDED by END or REASSIGN.
_DECIDED_EDGES = {
    ("PROPOSED", "ACTIVE"): frozenset({"ASSIGN"}),
    ("ACTIVE", "ENDED"): frozenset({"END", "REASSIGN"}),
}


class S015AssignmentCommandError(Exception):
    """Base class for this module's office-command errors; SUPPORT C by default."""
    support_class = "C"


class S015AssignmentAuthorityUnavailable(S015AssignmentCommandError):
    """An authority prerequisite for an office command is unavailable.

    Raised when the actor's Identity, credential, active membership or
    membership context fails its check (passed as the unavailable exception
    to _lock_actor, _read_actor_active_membership and
    _lock_actor_membership_context), or when the LO has no founding
    authority basis.
    """
    pass


class S015AssignmentNotFound(S015AssignmentCommandError):
    """A record an office command needs was not found.

    The membership or role definition named by a proposal, the assignment
    named by a transition, or the assignment's sequence-1 PROPOSE event or
    head event.
    """
    pass


class S015AssignmentEdgeRefused(S015AssignmentCommandError):
    """An office command asks for something outside what this service implements.

    Raised for a state, action or edge outside the implemented set, for
    PROPOSE sent as a transition, for a role definition belonging to another
    LO or not active, and for an authority reference that does not name the
    nominee's consent.
    """
    pass


class S015AssignmentStateUnused(S015AssignmentCommandError):
    """A state or action that v1.0 does not use (PRIVACY_TRANSFORMED, PRIVACY_TRANSFORM) was requested."""
    pass


class S015AssignmentTermRefused(S015AssignmentCommandError):
    """An appointment's term is missing, outside the vocabulary or inconsistent, or a term was supplied where none is allowed."""
    pass


class S015AssignmentMembershipRefused(S015AssignmentCommandError):
    """The nominee's membership does not allow the office act.

    Raised when a proposal names a PROBATIONARY or otherwise non-ACTIVE
    membership, and when ASSIGN finds the nominee's stored membership not
    ACTIVE or a departure recorded after the proposal.
    """
    pass


@dataclass(frozen=True, slots=True)
class ProposeAssignmentCommand:
    """Inputs to propose_assignment.

    The actor's credential; the request reference and idempotency key; the
    occurred and effective times; the membership and role definition UUIDs;
    and the proposed term (effective_until_state, with effective_until when
    FIXED). INTEVIA assigns received_at; a supplied value is refused.
    """
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime | None = field(default=None, kw_only=True)
    membership_uuid: UUID
    role_definition_uuid: UUID
    effective_until_state: str
    effective_until: datetime | None


@dataclass(frozen=True, slots=True)
class TransitionAssignmentCommand:
    """Inputs to transition_assignment.

    The actor's credential; the request reference and idempotency key; the
    occurred and effective times; the assignment UUID; the event action and
    requested resulting state; and the term, which ASSIGN must supply and END
    and REASSIGN must leave unset (both default to None). The intake boundary
    INTEVIA assigns received_at; a supplied value is refused.
    """
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime | None = field(default=None, kw_only=True)
    assignment_uuid: UUID
    action: str
    resulting_state: str
    # appointment content the appointing decision supplies (§3.3): required for ASSIGN,
    # refused for END and REASSIGN
    effective_until_state: str | None = None
    effective_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class AssignmentCommandResult:
    """An office command result, with fields that may be withheld when served.

    The receipt stores the serialized result before serve_replay applies
    viewer-specific withholding. The shared service applies serve_replay
    both when returning a newly completed command and on later replay.

    resulting_state, event_sequence, current_state_effective_at,
    state_known_at, state_source_event_set_fingerprint,
    suspension_tolls_term and suspension_tolls_term_source are replaced by
    WithheldField when the viewer lacks the SUBJECT audience; event_uuid
    too unless the event is a PROPOSE; effective_until and
    effective_until_state too when the event is an END or REASSIGN.

    Separately, authority_reference is replaced by WithheldField whenever
    _has_pk_valued_reference identifies it as primary-key-valued, regardless
    of the viewer's audience. These rules do not replace assignment_uuid,
    event_action, lineage_reference, temporal_basis_kind,
    temporal_basis_reference or authority_decision_reference.
    """
    assignment_uuid: UUID
    resulting_state: str | WithheldField
    event_uuid: UUID | WithheldField
    event_sequence: int | WithheldField
    event_action: str
    lineage_reference: str
    temporal_basis_kind: str | None
    temporal_basis_reference: str | None
    effective_until: datetime | None | WithheldField
    effective_until_state: str | WithheldField
    suspension_tolls_term: str | None | WithheldField
    suspension_tolls_term_source: str | None | WithheldField
    current_state_effective_at: datetime | WithheldField
    state_known_at: datetime | WithheldField
    state_source_event_set_fingerprint: str | WithheldField
    authority_reference: str | WithheldField
    authority_decision_reference: str

    def receipt_payload(self) -> dict[str, object]:
        """Serialize the pre-withholding result for storage in a command receipt.

        Returns seventeen fields keyed by name. Converts assignment_uuid and
        event_uuid to strings, and current_state_effective_at, state_known_at
        and a non-None effective_until to canonical timestamps. Copies the
        remaining field values directly.

        The shared command service calls this before serve_replay applies
        withholding. This is not a serializer for a viewer-filtered result:
        it neither rejects WithheldField values explicitly nor encodes them
        as structured withholding markers.
        """
        return {
            "assignment_uuid": str(self.assignment_uuid),
            "authority_decision_reference": self.authority_decision_reference,
            "authority_reference": self.authority_reference,
            "current_state_effective_at": canonical_timestamp(self.current_state_effective_at),
            "effective_until": (
                None if self.effective_until is None else canonical_timestamp(self.effective_until)
            ),
            "effective_until_state": self.effective_until_state,
            "event_action": self.event_action,
            "event_uuid": str(self.event_uuid),
            "event_sequence": self.event_sequence,
            "lineage_reference": self.lineage_reference,
            "resulting_state": self.resulting_state,
            "state_known_at": canonical_timestamp(self.state_known_at),
            "state_source_event_set_fingerprint": self.state_source_event_set_fingerprint,
            "suspension_tolls_term": self.suspension_tolls_term,
            "suspension_tolls_term_source": self.suspension_tolls_term_source,
            "temporal_basis_kind": self.temporal_basis_kind,
            "temporal_basis_reference": self.temporal_basis_reference,
        }

    @classmethod
    def replay_field_audiences(cls, result: "AssignmentCommandResult") -> dict[str, str]:
        """Return the replay audience of each restricted field.

        Marks resulting_state, event_sequence, current_state_effective_at,
        state_known_at, state_source_event_set_fingerprint,
        suspension_tolls_term and suspension_tolls_term_source as SUBJECT.

        Also marks event_uuid as SUBJECT unless event_action is PROPOSE, and
        effective_until and effective_until_state when event_action is END
        or REASSIGN.

        The shared viewer check identifies SUBJECT as the Identity linked
        through the assignment's membership; it does not require the assignment
        to be an active office. This method returns classifications only and
        does not itself withhold values.
        """
        audiences = {
            "resulting_state": "SUBJECT",
            "event_sequence": "SUBJECT",
            "current_state_effective_at": "SUBJECT",
            "state_known_at": "SUBJECT",
            "state_source_event_set_fingerprint": "SUBJECT",
            "suspension_tolls_term": "SUBJECT",
            "suspension_tolls_term_source": "SUBJECT",
        }
        if result.event_action != "PROPOSE":
            audiences["event_uuid"] = "SUBJECT"
        if result.event_action in {"END", "REASSIGN"}:
            audiences["effective_until"] = "SUBJECT"
            audiences["effective_until_state"] = "SUBJECT"
        return audiences

    @classmethod
    def from_receipt_payload(
        cls, payload: object,
    ) -> "AssignmentCommandResult":
        """Reconstruct an AssignmentCommandResult from a stored receipt payload, strictly.

        Requires a payload whose type is exactly dict and whose keys are exactly
        the seventeen result keys. Required textual fields must have type str;
        effective_until, the temporal basis fields and the tolling fields must
        be str or None. event_sequence must be a positive int, with bool refused.
        assignment_uuid and event_uuid must be canonical UUID strings.

        Accepts resulting_state PROPOSED, ACTIVE, PROBATIONARY, SUSPENDED or
        ENDED, and event_action PROPOSE, ASSIGN, END, REASSIGN or SUSPEND: the
        declared vocabularies minus PRIVACY_TRANSFORMED and PRIVACY_TRANSFORM.
        These are decoder vocabularies, not a claim that every corresponding
        command edge is implemented or that each action/state pairing is valid.

        effective_until_state must be FIXED, NONE or NOT_ESTABLISHED, with a
        non-None effective_until exactly when it is FIXED. temporal_basis_kind
        must be None, RETROSPECTIVE or PROSPECTIVE, and its reference must be
        None exactly when the kind is None. ASSIGN requires suspension_tolls_term
        "NO" and suspension_tolls_term_source "DEFAULT (no settings store)";
        other actions require both fields to be None. The two required
        timestamps and a non-None effective_until must pass
        _parse_canonical_timestamp.

        A receipt without event_uuid is malformed; no lookup recovers it
        (RC-1, item 0, the Human Governor's decision). A non-dict payload, or
        KeyError, TypeError or ValueError while checking or building the result,
        raises S015CommandReceiptMalformed with the cause chained.

        This decoder does not compare the result with its recorded event;
        the service's _verify_replay_result performs its separate, limited
        event check. It does not check timestamp ordering or reference contents
        beyond the stated type and pairing checks.
        """
        if type(payload) is not dict:
            raise S015CommandReceiptMalformed() from TypeError("receipt payload must be a dict")
        try:
            # RC-1 commission v1.3, item 1: enforce the writer's receipt contract.
            expected_keys = {
                "assignment_uuid",
                "authority_decision_reference",
                "authority_reference",
                "current_state_effective_at",
                "effective_until",
                "effective_until_state",
                "event_action",
                "event_uuid",
                "event_sequence",
                "lineage_reference",
                "resulting_state",
                "state_known_at",
                "state_source_event_set_fingerprint",
                "suspension_tolls_term",
                "suspension_tolls_term_source",
                "temporal_basis_kind",
                "temporal_basis_reference",
            }
            if set(payload) != expected_keys:
                raise ValueError("receipt fields do not match the role result contract")
            for key in (
                "assignment_uuid",
                "authority_decision_reference",
                "authority_reference",
                "current_state_effective_at",
                "effective_until_state",
                "event_action",
                "event_uuid",
                "lineage_reference",
                "resulting_state",
                "state_known_at",
                "state_source_event_set_fingerprint",
            ):
                if type(payload[key]) is not str:
                    raise TypeError(f"{key} must be a string")
            for key in (
                "effective_until",
                "suspension_tolls_term",
                "suspension_tolls_term_source",
                "temporal_basis_kind",
                "temporal_basis_reference",
            ):
                if payload[key] is not None and type(payload[key]) is not str:
                    raise TypeError(f"{key} must be a string or None")
            if type(payload["event_sequence"]) is not int or payload["event_sequence"] < 1:
                raise TypeError("event_sequence must be a positive integer")

            assignment_uuid = UUID(payload["assignment_uuid"])
            event_uuid = UUID(payload["event_uuid"])
            if (
                str(assignment_uuid) != payload["assignment_uuid"]
                or str(event_uuid) != payload["event_uuid"]
            ):
                raise ValueError("receipt UUIDs must use canonical strings")
            if payload["resulting_state"] not in (_STATE_VOCABULARY - _STATE_UNUSED):
                raise ValueError("resulting_state is outside the role state vocabulary")
            if payload["event_action"] not in (_ACTION_VOCABULARY - _ACTION_UNUSED):
                raise ValueError("event_action is outside the role result vocabulary")
            if payload["effective_until_state"] not in {"FIXED", "NONE", "NOT_ESTABLISHED"}:
                raise ValueError("effective_until_state is outside the role result vocabulary")
            if (payload["effective_until_state"] == "FIXED") != (payload["effective_until"] is not None):
                raise ValueError("effective_until does not match effective_until_state")
            if payload["temporal_basis_kind"] not in {None, "RETROSPECTIVE", "PROSPECTIVE"}:
                raise ValueError("temporal_basis_kind is outside the result vocabulary")
            if (payload["temporal_basis_kind"] is None) != (payload["temporal_basis_reference"] is None):
                raise ValueError("temporal basis reference does not match its kind")
            if payload["event_action"] == "ASSIGN":
                if (
                    payload["suspension_tolls_term"] != _TOLLING_VALUE
                    or payload["suspension_tolls_term_source"] != _TOLLING_SOURCE
                ):
                    raise ValueError("ASSIGN receipt lacks its tolling snapshot")
            elif (
                payload["suspension_tolls_term"] is not None
                or payload["suspension_tolls_term_source"] is not None
            ):
                raise ValueError("non-ASSIGN receipt contains tolling fields")

            effective_until = (
                None
                if payload["effective_until"] is None
                else _parse_canonical_timestamp(payload["effective_until"])
            )
            return cls(
                assignment_uuid=assignment_uuid,
                resulting_state=payload["resulting_state"],
                event_uuid=event_uuid,
                event_sequence=payload["event_sequence"],
                event_action=payload["event_action"],
                lineage_reference=payload["lineage_reference"],
                temporal_basis_kind=payload["temporal_basis_kind"],
                temporal_basis_reference=payload["temporal_basis_reference"],
                effective_until=effective_until,
                effective_until_state=payload["effective_until_state"],
                suspension_tolls_term=payload["suspension_tolls_term"],
                suspension_tolls_term_source=payload["suspension_tolls_term_source"],
                current_state_effective_at=_parse_canonical_timestamp(payload["current_state_effective_at"]),
                state_known_at=_parse_canonical_timestamp(payload["state_known_at"]),
                state_source_event_set_fingerprint=payload["state_source_event_set_fingerprint"],
                authority_reference=payload["authority_reference"],
                authority_decision_reference=payload["authority_decision_reference"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise S015CommandReceiptMalformed() from exc


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


def _validate_term(
    *,
    effective_until_state: object,
    effective_until: object,
    effective_at: datetime,
) -> tuple[str, datetime | None]:
    """Validate an appointment's term and return (effective_until_state, effective_until).

    Validates effective_until_state as a canonical key of at most 15
    characters, which must be FIXED, NONE or NOT_ESTABLISHED. effective_until
    is required exactly when the state is FIXED; when given, it is validated
    as a timestamp and must be later than effective_at. Each failure raises
    S015AssignmentTermRefused, except the canonical-key and timestamp checks,
    which raise their own TypeError or ValueError. Writes nothing.
    """
    state = _canonical_key(effective_until_state, "effective_until_state", 15)
    if state not in {"FIXED", "NONE", "NOT_ESTABLISHED"}:
        raise S015AssignmentTermRefused(f"effective_until_state {state} is not in the landed vocabulary")
    if (state == "FIXED") != (effective_until is not None):
        raise S015AssignmentTermRefused(
            "effective_until is required exactly when effective_until_state is FIXED"
        )
    if effective_until is None:
        return state, None
    until = _validate_timestamp(effective_until, "effective_until")
    if until <= effective_at:
        raise S015AssignmentTermRefused("effective_until must be later than effective_at")
    return state, until


def _validate_resulting_state(value: object) -> str:
    """Validate a requested resulting state and return it.

    Validates it as a canonical key of at most 19 characters. Raises
    S015AssignmentStateUnused for PRIVACY_TRANSFORMED (unused in v1.0), and
    S015AssignmentEdgeRefused for a state outside PROPOSED, ACTIVE,
    PROBATIONARY, SUSPENDED and ENDED.
    """
    state = _canonical_key(value, "resulting_state", 19)
    if state in _STATE_UNUSED:
        raise S015AssignmentStateUnused(
            f"assignment state {state} is unused by v1.0 (⟨B.050⟩)"
        )
    if state not in _STATE_VOCABULARY:
        raise S015AssignmentEdgeRefused(f"assignment state {state} is not decided")
    return state


def _validate_event_action(value: object) -> str:
    """Validate a transition's event action and return it.

    Validates it as a canonical key of at most 17 characters. Raises
    S015AssignmentStateUnused for PRIVACY_TRANSFORM (unused in v1.0), and
    S015AssignmentEdgeRefused for an action outside the vocabulary or for
    PROPOSE, which only propose_assignment performs.
    """
    action = _canonical_key(value, "action", 17)
    if action in _ACTION_UNUSED:
        raise S015AssignmentStateUnused(
            f"assignment action {action} is unused by v1.0 (⟨B.050⟩)"
        )
    if action not in _ACTION_VOCABULARY:
        raise S015AssignmentEdgeRefused(f"assignment action {action} is not decided")
    if action == "PROPOSE":
        raise S015AssignmentEdgeRefused(
            "assignment action PROPOSE is propose_assignment's act, not a transition"
        )
    return action


def _validate_assignment_edge(*, current_state: str, action: str, target_state: str) -> None:
    """Check an office edge against this service's implemented transition set.

    First raises S015OperationNotYetImplemented for role suspension
    (IL-4, S2b) when action is SUSPEND or the state pair is ACTIVE to
    SUSPENDED or SUSPENDED to ACTIVE, regardless of the other argument.

    If that suspension check does not apply, allows only PROPOSED to ACTIVE
    by ASSIGN and ACTIVE to ENDED by END or REASSIGN. Every other combination,
    including PROPOSED to ENDED and edges involving PROBATIONARY, raises
    S015AssignmentEdgeRefused.

    Returns None for an allowed combination. Performs no writes or authority
    qualification; _execute_transition calls it after authority qualification.
    """
    if (
        action == "SUSPEND"
        or (current_state == "ACTIVE" and target_state == "SUSPENDED")
        or (current_state == "SUSPENDED" and target_state == "ACTIVE")
    ):
        raise S015OperationNotYetImplemented("role_suspension", "IL-4", "S2b")
    allowed = _DECIDED_EDGES.get((current_state, target_state))
    if allowed is None or action not in allowed:
        raise S015AssignmentEdgeRefused(
            f"assignment edge {current_state} -> {target_state} by {action} is refused"
        )


def _insert_assignment_event_row(
    *,
    database_alias: str,
    event_uuid: UUID,
    assignment_id: int,
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
) -> int:
    """Execute a raw SQL insert of one assignment state event and return its id.

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
                "SELECT l1_commitment FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                [predecessor_id],
            )
            predecessor_row = cursor.fetchone()
            predecessor_l1_commitment = None if predecessor_row is None else predecessor_row[0]
        cursor.execute(
            "SELECT public.s015_0022_digest('s015p1', public.s015_0022_parts_preimage(%s, %s::uuid))",
            ["core_contextualroleassignmentstateevent", event_uuid],
        )
        parts_commitment = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO public.core_contextualroleassignmentstateevent (
                event_uuid,
                assignment_id,
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
                resulting_state
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s
            ) RETURNING id
            """,
            [
                event_uuid,
                assignment_id,
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
            ],
        )
        return cursor.fetchone()[0]


def _head_assign_term(
    *,
    database_alias: str,
    assignment_id: int,
) -> tuple[str, datetime | None] | None:
    """Return the term recorded on the assignment's latest ASSIGN event, or None.

    Reads effective_until_state and effective_until from the ASSIGN event
    with the highest sequence for this assignment, by raw SQL on
    database_alias. Returns None when no ASSIGN event exists. Does not look
    at later END or REASSIGN events.
    """
    with connections[database_alias].cursor() as cursor:
        cursor.execute(
            """
            SELECT effective_until_state, effective_until
              FROM public.core_contextualroleassignmentstateevent
             WHERE assignment_id = %s AND action = 'ASSIGN'
             ORDER BY sequence DESC
             LIMIT 1
            """,
            [assignment_id],
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return str(row[0]), row[1]


def assignment_in_effect(
    assignment: ContextualRoleAssignment,
    at: datetime,
    *,
    database_alias: str = "default",
) -> bool:
    """Apply the recorded fixed-term expiry check to the supplied assignment's state.

    Requires type(assignment) to be ContextualRoleAssignment, otherwise
    raises TypeError. Validates at as a timestamp and reads the latest
    ASSIGN event's term from database_alias.

    Returns False when that term is FIXED, effective_until is present,
    and at is at or after effective_until. Otherwise returns whether
    the supplied assignment object's state is ACTIVE.

    Does not refresh the supplied object, fold events at at, check the
    appointment's start against at, or filter the term read by effective
    or knowledge time. This is not a historical as-of-state query.

    Applies no tolling and performs no writes. Reaching the fixed-term end
    changes this return value without recording an ending.
    """
    if type(assignment) is not ContextualRoleAssignment:
        raise TypeError("assignment must be a ContextualRoleAssignment")
    moment = _validate_timestamp(at, "at")
    term = _head_assign_term(database_alias=database_alias, assignment_id=assignment.pk)
    if term is not None and term[0] == "FIXED" and term[1] is not None and moment >= term[1]:
        return False
    return assignment.state == "ACTIVE"


OFFICE_ACTION_MESSAGES = {
    'C_NOMINATION_ROUTE_NOT_IMPLEMENTED': ('Nominating someone for this office is not available. The nomination route recorded for this office is not implemented in this release. No nomination has been made. You cannot complete this nomination in this release; repeating it will not help. Coordinators cannot nominate in place of the recorded route.', 'B'),
    'C_APPOINTING_ROUTE_NOT_IMPLEMENTED': ('Appointing someone to this office is not available. The body named in the office definition has no implemented appointment route, and no alternative route is recorded. No appointment has been made. You cannot complete this appointment through the named body in this release; repeating it will not help. Coordinators cannot appoint in its place without a recorded alternative route.', 'B'),
    'E_NOMINEE_NOT_ELIGIBLE': ('Nominating or appointing this person to this office was refused. The office requires current GOVERNANCE participation, and the person has no qualifying participation at the time of this action. No nomination or appointment has been made by this request. Choose an eligible nominee, or wait until this person has qualifying participation before trying again.', 'B'),
    'E_CUSTOM_ROLE_NOT_ENTITLED': ('Nominating someone for this custom role was refused. Custom roles need an active subscription or complimentary access, and this organisation did not have it when the request was made. No nomination has been made; existing nominations and appointments are unchanged. Once the organisation has that access, make a new nomination.', 'A'),
    'C_RESERVED_ROOT_OFFICE': ('Nominating, appointing, ending or reassigning this reserved INTEVIA office was refused. Reserved INTEVIA offices cannot be managed through this organisation-office route. No office has been changed. This office is managed by INTEVIA itself; there is nothing for you to do.', 'B'),
}

OFFICE_REFUSAL_RULE_NAMES = {
    'C_GATEWAY_NOT_OPEN': 'the organisation has not opened the required application gateway',
    'C_REFERENCE_UNREPRESENTABLE': 'the required authority evidence cannot be represented within the supported record format',
    'E_NOMINATION_POLICY_NOT_ENTITLED': 'the organisation lacks the entitlement needed for this nomination policy',
    'C_MEMBERSHIP_TRANSITION_NOT_IMPLEMENTED': 'the requested membership transition is not implemented',
    'C_BOARD_OFFICE_PERMANENT': 'a board office is permanent and cannot be retired',
    'C_COORDINATOR_DEFINITION_RESERVED': 'the Coordinator office is established at founding and cannot be defined here',
    'C_DEFINITION_KIND_FIXED': 'an office definition cannot change between a board office and a custom role',
    'E_NOT_APPLICANT': 'only the applicant can take this action',
    'E_NOT_REGISTERED': 'the actor has no qualifying registration at the required time',
    'E_NOT_ASSIGNEE': 'only the current assignee can take this application action',
    'UNRECOGNISED_ROUTE': 'the requested authority route is not recognised',
    'DENIED': 'you do not have authority for this action, and no further reason can be given',
    'UNRECOGNISED_ACTION': 'the requested office action or its supplied references are not recognised',
    'E_NOT_EFFECTIVE_COORDINATOR': 'this action requires an effective Coordinator of this organisation',
    'C_COORDINATOR_RECOVERY_NOT_IMPLEMENTED': 'there is no eligible Coordinator and the required recovery route is not implemented',
    'C_ROUTE_IS_CQ': 'this organisation uses Collective Questioning for this action',
    'C_OFFICE_CONSENT_ABSENT_OR_WITHDRAWN': 'the required consent is absent, withdrawn or does not permit this action',
    'C_LO_NO_CAPACITY': 'this organisation lacks the required recorded capacity',
    'C_LO_PAUSED': 'this organisation is paused',
    'C_LO_CLOSED': 'this organisation is closed',
    'E_IDENTITY_INACTIVE': 'the acting identity is not active',
    'C_CONFIGURATION_NOT_ESTABLISHED': 'the organisation has no established governance settings',
    'SBG_LO_NOT_ACTIVE': 'this organisation is not active',
    'SBG_SEAT_ROUTE_UNAVAILABLE': 'the required GOVERNANCE seat route is unavailable',
}

OFFICE_ACTION_NAMES = {
    'PROPOSE': 'Nominating someone for this office',
    'ASSIGN': 'Appointing someone to this office',
    'END': 'Ending this office appointment',
    'REASSIGN': 'Ending this appointment for reassignment',
    'CONSENT': 'Giving consent to this office nomination',
    'DECLINE': 'Declining this office nomination',
    'WITHDRAW': 'Withdrawing consent to this office nomination',
}

OFFICE_FALLBACK_MESSAGES = {
    'GOVERNED': '{action} was refused under this rule: {rule}. Nothing has changed; your request and this refusal are on record (reference {decision_reference}). Check that rule before making a new request; repeating the same request will get the same answer.',
    'NOT_AVAILABLE': '{action} is not yet available in this release. Nothing has changed; your request is on record. You cannot complete it in this release, so there is no need to repeat it.',
}


OFFICE_KNOWN_ERROR_MESSAGES = {
    'REPLAY_MISMATCH': ('{action} was refused. This request uses the same reference as an earlier request but with different details, so it cannot be treated as a repeat of it. Nothing has changed; the earlier request stands as it was. To make a different change, start a new request.', 'A'),
    'NOMINATION_NOT_FOUND': ("{action} was refused. The nomination could not be found in this organisation. Nothing has changed. Open the nomination from this organisation's current list and try again.", 'A'),
}


def office_action_support_details(error, action="Changing this office"):
    """Render only disclosed rules and known action names; never infer private causes."""
    support_pending = "INTEVIA could not confirm the outcome of this action. Check its current record before trying again. INTEVIA Support needs the error details to investigate."
    from src.intevia.services.organism_office_consent_service import S015OfficeConsentNotFound
    if isinstance(error, S015CommandReplayMismatch):
        text, category = OFFICE_KNOWN_ERROR_MESSAGES["REPLAY_MISMATCH"]
        return category, text.format(action=action)
    if isinstance(error, S015OfficeConsentNotFound):
        text, category = OFFICE_KNOWN_ERROR_MESSAGES["NOMINATION_NOT_FOUND"]
        return category, text.format(action=action)
    if isinstance(error, S015AuthorityDenied):
        code = getattr(error.disclosed_refusal_code, "value", None)
        if error.authority_decision_reference is not None:
            if code in OFFICE_ACTION_MESSAGES:
                text, category = OFFICE_ACTION_MESSAGES[code]
                return category, text
            rule = OFFICE_REFUSAL_RULE_NAMES.get(code, "a rule for which this release has no plain-English explanation; the specific cause cannot be stated here")
            return "B", OFFICE_FALLBACK_MESSAGES["GOVERNED"].format(
                action=action, rule=rule, decision_reference=error.authority_decision_reference)
    if isinstance(error, S015OperationNotYetImplemented):
        return "B", OFFICE_FALLBACK_MESSAGES["NOT_AVAILABLE"].format(action=action)
    return "C", support_pending


class OrganismRoleService(_OrganismCommandService):
    """The office command service: propose_assignment and transition_assignment, and two reads.

    Declares, for the shared _OrganismCommandService, the action
    TRANSITION_CONTEXTUAL_ROLE, S015AssignmentNotFound as its not-found
    exception and S015AssignmentAuthorityUnavailable as its unavailable
    exception; rebuilds replayed results from receipt payloads and, on
    replay, checks each against its recorded assignment event.
    """
    _action = _ACTION
    _not_found = S015AssignmentNotFound
    _authority_unavailable = S015AssignmentAuthorityUnavailable

    def _replay_result(self, payload: object) -> AssignmentCommandResult:
        """Rebuild a replayed result from its receipt payload (AssignmentCommandResult.from_receipt_payload)."""
        return AssignmentCommandResult.from_receipt_payload(payload)

    def _verify_replay_result(self, result: AssignmentCommandResult) -> None:
        """Check a replayed office result against its recorded assignment event.

        Raises TypeError unless result is an AssignmentCommandResult instance.
        Reads the event identified by the result's assignment UUID and event
        sequence. Requires its event UUID, action and lineage reference to equal
        the result's. A missing row or any disagreement raises
        S015CommandReceiptMalformed with a ValueError cause (RC-1, item 7).

        This check does not compare the other result fields with the event or
        recompute its lineage reference. Database-read errors propagate.
        """
        if not isinstance(result, AssignmentCommandResult):
            raise TypeError("role replay result has the wrong type")
        # RC-1 commission v1.3, item 7 (A2-b): agree with the chain only on replay.
        event = (
            ContextualRoleAssignmentStateEvent.objects.using(self._alias)
            .filter(
                assignment__assignment_uuid=result.assignment_uuid,
                sequence=result.event_sequence,
            )
            .values_list("event_uuid", "action", "lineage_reference")
            .first()
        )
        if event is None or event != (
            result.event_uuid,
            result.event_action,
            result.lineage_reference,
        ):
            raise S015CommandReceiptMalformed() from ValueError(
                "receipt does not agree with its role chain event"
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
        """Return the command fingerprint for a role command.

        Delegates to _command_payload with this module's command domain, schema
        and action, the actor's Identity UUID and access epoch, the request
        reference, the idempotency key, the occurred time, and the supplied
        target and command dictionaries.
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

    def propose_assignment(self, command: ProposeAssignmentCommand) -> AssignmentCommandResult:
        """Execute once and attach the SUPPORT classification without changing the outcome."""
        try:
            return self._propose_assignment_with_intake(command)
        except Exception as exc:
            try:
                exc.support_class, exc.support_message = office_action_support_details(exc, "Nominating someone for this office")
            except Exception:
                pass
            raise

    def _propose_assignment_with_intake(self, command: ProposeAssignmentCommand) -> AssignmentCommandResult:
        """Propose an office assignment: admits the request through the shared
        intake, then runs _execute_propose with the bound request within
        authority_atomic on this service's database alias and returns its
        result.

        A matching completed request is reconstructed from its receipt and
        served through the replay viewer rules; a changed request under an
        occupied key is refused at the intake.
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

        entry = IntakeEntry.PROPOSE_ASSIGNMENT
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("PROPOSE_ASSIGNMENT is not active")
        shape = intake_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_propose_assignment(command, bound)

    def _execute_propose_assignment(
        self, command: ProposeAssignmentCommand, bound: BoundRequest,
    ) -> AssignmentCommandResult:
        """Execute an admitted proposal inside the service's authority transaction."""
        with authority_atomic(self._alias, received_at=bound.received_at):
            return self._execute_propose(command, bound=bound)

    def transition_assignment(self, command: TransitionAssignmentCommand) -> AssignmentCommandResult:
        """Execute once and attach the SUPPORT classification without changing the outcome."""
        try:
            return self._transition_assignment_with_intake(command)
        except Exception as exc:
            try:
                exc.support_class, exc.support_message = office_action_support_details(exc, OFFICE_ACTION_NAMES.get(command.action, "Changing this office"))
            except Exception:
                pass
            raise

    def _transition_assignment_with_intake(self, command: TransitionAssignmentCommand) -> AssignmentCommandResult:
        """Appoint or end an office assignment: admits the request through the
        shared intake, then runs _execute_transition with the bound request
        within authority_atomic on this service's database alias and returns
        its result.

        REASSIGN ends the existing assignment; a replacement appointment
        requires a new assignment anchor.

        A matching completed request is reconstructed from its receipt and
        served through the replay viewer rules; a changed request under an
        occupied key is refused at the intake.
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

        entry = IntakeEntry.TRANSITION_ASSIGNMENT
        _require_admission_state(self._alias, entry)
        if entry not in ACTIVE_ENTRIES:
            raise IntakeAdmissionTechnicalFailure("TRANSITION_ASSIGNMENT is not active")
        shape = intake_shape(entry, command)
        actor_ref = _resolve_actor(self._alias, command.credential)
        bound = admit_request(self._alias, entry, actor_ref, shape)
        return self._execute_transition_assignment(command, bound)

    def _execute_transition_assignment(
        self, command: TransitionAssignmentCommand, bound: BoundRequest,
    ) -> AssignmentCommandResult:
        """Execute an admitted assignment transition inside authority_atomic.

        Passes the bound request's received_at to authority_atomic. A
        C_FOUNDER_PROTECTED denial from authority qualification, which carries
        a decision reference, is raised again as S015AuthorityDenied with the
        restated text and its recorded-refusal tail, keeping its codes and
        decision reference, with the original denial as its cause.
        """
        try:
            with authority_atomic(self._alias, received_at=bound.received_at):
                return self._execute_transition(command, bound=bound)
        except S015AuthorityDenied as exc:
            if (
                exc.disclosed_refusal_code is not S015AuthorityRefusalCode.C_FOUNDER_PROTECTED
                or exc.authority_decision_reference is None
            ):
                raise
            message = _intake_tail(
                "The office was not ended or reassigned. Until this LO is born, the founder's original "
                "Coordinator appointment can be ended or reassigned only by the founder. This is a "
                "governed protection of the LO's founding, not a technical fault. After Birthing it no "
                "longer applies: the appointment can then be ended or reassigned under the LO's ordinary "
                "governed rules. INTEVIA recorded this refusal and that the request was received at "
                "{received_at}; nothing else was saved.",
                bound.received_at,
            )
            raise S015AuthorityDenied(
                message,
                refusal_code=exc.refusal_code,
                disclosed_refusal_code=exc.disclosed_refusal_code,
                authority_decision_reference=exc.authority_decision_reference,
                request_uuid=exc.request_uuid,
            ) from exc

    def assignment_in_effect(self, assignment: ContextualRoleAssignment, at: datetime) -> bool:
        """Apply assignment_in_effect using this service's database alias.

        Uses the supplied object's state and the latest recorded ASSIGN term,
        with at used for the fixed-term expiry check. Does not refresh the
        object or reconstruct its historical state.
        """
        return assignment_in_effect(assignment, at, database_alias=self._alias)

    def open_nominations(
        self,
        living_organism_uuid: UUID,
        *,
        effective_at: datetime,
        known_at: datetime,
    ) -> list[ContextualRoleAssignment]:
        """Select the LO's open nominations at supplied effective and knowledge times.

        Validates the LO UUID and both timestamps. Returns an empty list when
        the LO is absent.

        Selects assignment ids whose s015_effective_state projection at
        effective_at and known_at is PROPOSED, excluding any assignment with
        a DECLINE or WITHDRAW consent record whose effective_at and recorded_at
        are no later than those respective bounds. The exclusion matches the
        assignment, without separately matching a proposal or nominee.

        Then loads the selected assignments with membership and role_definition,
        ordered by id. These are model rows loaded at retrieval time, not
        historical projections of those rows at the supplied coordinates.

        This method performs no viewer-authorisation check and requests no
        explicit row locks.
        """
        organism_uuid = _canonical_uuid(living_organism_uuid, "living_organism_uuid")
        effective_moment = _validate_timestamp(effective_at, "effective_at")
        known_moment = _validate_timestamp(known_at, "known_at")
        try:
            organism = LivingOrganism.objects.using(self._alias).get(organism_id=organism_uuid)
        except LivingOrganism.DoesNotExist:
            return []
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT a.id FROM public.core_contextualroleassignment a "
                "JOIN public.core_organismmembership m ON m.id = a.membership_id "
                "CROSS JOIN LATERAL public.s015_effective_state("
                "'core_contextualroleassignment', a.id, %s, %s) folded "
                "WHERE m.living_organism_id = %s AND folded.projection = 'PROPOSED' "
                "AND NOT EXISTS ("
                "SELECT 1 FROM public.core_officeappointmentconsent c "
                "WHERE c.assignment_id = a.id AND c.action IN ('DECLINE', 'WITHDRAW') "
                "AND c.effective_at <= %s AND c.recorded_at <= %s) "
                "ORDER BY a.id",
                [effective_moment, known_moment, organism.pk, effective_moment, known_moment],
            )
            assignment_ids = [row[0] for row in cursor.fetchall()]
        assignments = ContextualRoleAssignment.objects.using(self._alias).filter(
            pk__in=assignment_ids,
        ).select_related("membership", "role_definition").order_by("id")
        return list(assignments)

    def _execute_propose(
        self, command: ProposeAssignmentCommand, *, bound: BoundRequest,
    ) -> AssignmentCommandResult:
        """Execute an office proposal within the caller's authority_atomic block.

        Validates the request reference, idempotency key, timestamps, UUIDs and
        proposed term; locks the actor, takes the command mutex and reads the
        nominee's membership with its Identity and LO. A missing membership
        raises S015AssignmentNotFound. Builds target and command fingerprints
        including the proposed term and default no-tolling value, and returns
        a replay result if _attempt_replay supplies one.

        For a fresh command, checks the actor's stored ACTIVE membership before
        acquiring the registered LO lock. Re-reads and locks the nominee's
        membership with of=("self",), without requesting locks on its joined
        Identity or LO, then locks the actor's membership context and qualifies
        authority.

        If qualification returns, the local checks proceed in this order:
        require a founding authority basis and lock its row; resolve and lock
        the role definition; require it to belong to the same LO and be active;
        refuse non-LO scope, then a code other than COORDINATOR; refuse a
        PROBATIONARY nominee, then any other stored state that is not ACTIVE.

        These checks raise S015AssignmentAuthorityUnavailable for a missing
        basis, S015AssignmentNotFound for an unknown definition,
        S015AssignmentEdgeRefused for a different LO or inactive definition,
        S015OperationNotYetImplemented for unsupported scope or code, and
        S015AssignmentMembershipRefused for the nominee's state. Earlier actor
        checks or authority qualification can refuse before these checks.

        Creates a PROPOSED assignment and its sequence-1 PROPOSE event.
        The event and result carry effective_until_state NOT_ESTABLISHED and
        effective_until None; the proposed term is bound into fingerprints,
        not established as the event's term. Refreshes the governed cache and
        finalises the receipt.
        """
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(bound.received_at, "received_at")
        membership_uuid = _canonical_uuid(command.membership_uuid, "membership_uuid")
        role_definition_uuid = _canonical_uuid(command.role_definition_uuid, "role_definition_uuid")
        effective_until_state, effective_until = _validate_term(
            effective_until_state=command.effective_until_state,
            effective_until=command.effective_until,
            effective_at=effective_at,
        )

        actor = _lock_actor(
            database_alias=self._alias,
            credential=command.credential,
            unavailable=S015AssignmentAuthorityUnavailable,
        )
        from src.intevia.services.command_intake import IntakeEntry

        bound.verify(command, actor, IntakeEntry.PROPOSE_ASSIGNMENT, self._alias)
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
            raise S015AssignmentNotFound("membership not found") from exc
        organism = membership.living_organism

        # code is immutable: this fingerprint read needs no row or LO lock.
        source_code = OrganismRoleDefinition.objects.using(self._alias).filter(
            role_uuid=role_definition_uuid, living_organism=organism,
            scope=OrganismRoleDefinition.Scope.LIVING_ORGANISM,
        ).values_list("code", flat=True).first()
        target = {
            "action": _ACTION.value,
            "circle": None,
            "event_action": "PROPOSE",
            "effective_until": None if effective_until is None else canonical_timestamp(effective_until),
            "effective_until_state": effective_until_state,
            "living_organism_id": str(organism.organism_id),
            "membership_uuid": str(membership_uuid),
            "resulting_state": _OPEN_STATE,
            "role_definition_uuid": str(role_definition_uuid),
            "schema": _TARGET_SCHEMA,
            "suspension_tolls_term": _TOLLING_VALUE,
            "suspension_tolls_term_source": _TOLLING_SOURCE,
        }
        if source_code is not None and source_code != _COORDINATOR_ROLE_CODE:
            target["nomination_rule_source"] = "DEFINITION"
        target_fingerprint = authority_target_fingerprint(target)
        command_fingerprint = self._command_fingerprint(
            actor=actor,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            target=target,
            command={
                "effective_at": canonical_timestamp(effective_at),
                "effective_until": None if effective_until is None else canonical_timestamp(effective_until),
                "effective_until_state": effective_until_state,
                "membership_uuid": str(membership_uuid),
                "received_at": canonical_timestamp(received_at),
                "resulting_state": _OPEN_STATE,
                "role_definition_uuid": str(role_definition_uuid),
                "schema": _COMMAND_SCHEMA,
                "suspension_tolls_term": _TOLLING_VALUE,
                "suspension_tolls_term_source": _TOLLING_SOURCE,
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

        _read_actor_active_membership(
            database_alias=self._alias,
            actor_identity_pk=actor.pk,
            living_organism_pk=membership.living_organism_id,
            unavailable=S015AssignmentAuthorityUnavailable,
        )
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
        organism = membership.living_organism
        actor_membership, actor_role_refs = _lock_actor_membership_context(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            living_organism_id=organism.organism_id,
            unavailable=S015AssignmentAuthorityUnavailable,
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
            target=target,
            living_organism_id=organism.organism_id,
        )

        basis_id = organism.founding_authority_basis_id
        if basis_id is None:
            raise S015AssignmentAuthorityUnavailable(
                "organism founding authority basis is required"
            )
        basis = (
            AuthorityBasis.objects.using(self._alias)
            .select_for_update()
            .get(pk=basis_id)
        )
        try:
            role_definition = (
                OrganismRoleDefinition.objects.using(self._alias)
                .select_for_update()
                .get(role_uuid=role_definition_uuid)
            )
        except OrganismRoleDefinition.DoesNotExist as exc:
            raise S015AssignmentNotFound("role definition not found") from exc
        if role_definition.living_organism_id != organism.pk:
            raise S015AssignmentEdgeRefused(
                "role definition and membership must share an organism"
            )
        if not role_definition.active:
            raise S015AssignmentEdgeRefused("role definition is not active")
        # scope before code: a Circle-scoped office of any kind is S4's (IL-6)
        if role_definition.scope != OrganismRoleDefinition.Scope.LIVING_ORGANISM:
            raise S015OperationNotYetImplemented("circle_scoped_office", "IL-6", "S4")
        if role_definition.code != _COORDINATOR_ROLE_CODE:
            raise S015OperationNotYetImplemented("appoint_non_coordinator_office", "IL-8", "SR")
        if membership.state == "PROBATIONARY":
            raise S015AssignmentMembershipRefused(
                "a PROBATIONARY membership holds no COORDINATOR office (⟨E2.114⟩)"
            )
        if membership.state != "ACTIVE":
            raise S015AssignmentMembershipRefused(
                "an office requires a current membership (⟨C.037⟩)"
            )

        assignment_uuid = uuid4()
        assignment = ContextualRoleAssignment.objects.using(self._alias).create(
            assignment_uuid=assignment_uuid,
            membership=membership,
            role_definition=role_definition,
            state=_OPEN_STATE,
            current_state_effective_at=effective_at,
            state_known_at=received_at,
            state_source_event_set_fingerprint=_empty_anchor_fingerprint(
                database_alias=self._alias,
                anchor_token=_ANCHOR_TOKEN,
                anchor_uuid=assignment_uuid,
            ),
        )
        event_id = self._write_event(
            assignment=assignment,
            actor=actor,
            sequence=1,
            predecessor=None,
            event_action="PROPOSE",
            # the term is the appointment's, not the proposal's: a chain row's effective_until
            # states when that event's effect ends, so the PROPOSE row establishes no term
            effective_until=None,
            effective_until_state="NOT_ESTABLISHED",
            decision=decision,
            basis_id=basis.pk,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=command_fingerprint,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            prior_state=None,
            resulting_state=_OPEN_STATE,
        )
        result = self._refresh_and_result(
            assignment=assignment,
            event_id=event_id,
            # the result describes the written row: the PROPOSE row establishes no term; the
            # proposed term stays proposal content in the fingerprints (the Reviewer's S2 F4)
            effective_until=None,
            effective_until_state="NOT_ESTABLISHED",
            with_tolling=False,
            decision=decision,
            effective_at=effective_at,
        )
        return self._finalise(
            actor=actor,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=command_fingerprint,
            result=result,
            received_at=bound.received_at,
        )

    def _execute_transition(
        self, command: TransitionAssignmentCommand, *, bound: BoundRequest,
    ) -> AssignmentCommandResult:
        """Execute an office transition within the caller's authority_atomic block.

        Validates the request reference, idempotency key, timestamps, assignment
        UUID, event action and requested resulting state. ASSIGN requires
        effective_until_state and validates its term through _validate_term;
        every other admitted action rejects supplied term fields. Missing or
        inconsistent term content raises S015AssignmentTermRefused; canonical
        key and timestamp validation can also raise TypeError or ValueError.

        Locks the actor, takes the command mutex and reads the assignment with
        its membership, LO, Identity and role definition, raising
        S015AssignmentNotFound if the assignment is absent. Builds the target
        and command fingerprints; ASSIGN includes its term and the default
        no-tolling value and source. Returns a replay result if available.

        For a fresh command, performs an early stored-ACTIVE actor-membership
        check for ASSIGN, REASSIGN and another person's END. Self-END skips
        only that early check. ASSIGN then locks the LO row directly; other
        actions acquire the registered LO lock and lock the assignment and
        membership using of=("self", "membership"). Every action then obtains
        the actor's locked membership context, requiring ACTIVE state and a
        head transition, and qualifies authority.

        After qualification, ASSIGN locks the assignment and its joined
        membership and role definition. It raises S015AssignmentMembershipRefused
        unless the nominee's stored membership state is ACTIVE. It then requires
        a sequence-1 PROPOSE event, raising S015AssignmentNotFound if absent,
        and refuses if an ENDED membership transition was recorded after that
        proposal. It verifies the nominee's consent against the authority
        reference through _verify_assign_consent.

        Next, in order, it refuses non-LO scope and non-COORDINATOR codes with
        S015OperationNotYetImplemented; validates the current state and edge;
        requires a head event; and refuses an event whose
        (effective_at, occurred_at, received_at) sorts before the head's tuple.

        For END or REASSIGN, where a founder is recorded and the actor is not
        that founder, an assignment recognised as the founding appointment is
        protected while no Birthing fact is eligible at effective_at and
        received_at. This raises S015AuthorityDenied with C_FOUNDER_PROTECTED.

        Requires a founding authority basis, raising
        S015AssignmentAuthorityUnavailable if its reference is absent, and
        locks the basis row. Writes the next event at head sequence plus one:
        ASSIGN writes its validated term; END and REASSIGN write
        NOT_ESTABLISHED with no effective_until.

        Refreshes the governed cache and constructs the result. The result
        includes the default tolling value and source only for ASSIGN; these
        are not cache-refresh inputs or separate event columns. Finalises the
        receipt and serves the result through the viewer rules.
        """
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(bound.received_at, "received_at")
        assignment_uuid = _canonical_uuid(command.assignment_uuid, "assignment_uuid")
        event_action = _validate_event_action(command.action)
        target_state = _validate_resulting_state(command.resulting_state)
        # the appointing decision supplies the term (§3.3): required for ASSIGN, refused otherwise
        if event_action == "ASSIGN":
            if command.effective_until_state is None:
                raise S015AssignmentTermRefused(
                    "ASSIGN requires effective_until_state (the appointing decision supplies the term)"
                )
            assign_term = _validate_term(
                effective_until_state=command.effective_until_state,
                effective_until=command.effective_until,
                effective_at=effective_at,
            )
        else:
            if command.effective_until_state is not None or command.effective_until is not None:
                raise S015AssignmentTermRefused(
                    f"only ASSIGN establishes a term; effective_until fields are refused on {event_action}"
                )
            assign_term = None

        actor = _lock_actor(
            database_alias=self._alias,
            credential=command.credential,
            unavailable=S015AssignmentAuthorityUnavailable,
        )
        from src.intevia.services.command_intake import IntakeEntry

        bound.verify(command, actor, IntakeEntry.TRANSITION_ASSIGNMENT, self._alias)
        acquire_organism_command_mutex(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            action=_ACTION,
            idempotency_key=idempotency_key,
        )

        try:
            assignment = (
                ContextualRoleAssignment.objects.using(self._alias)
                .select_related("membership__living_organism", "membership__identity", "role_definition")
                .get(assignment_uuid=assignment_uuid)
            )
        except ContextualRoleAssignment.DoesNotExist as exc:
            raise S015AssignmentNotFound("assignment not found") from exc
        organism = assignment.membership.living_organism

        # the appointed term is bound into ASSIGN's target fingerprint, so the qualified
        # decision names the exact appointment content
        target: dict[str, object] = {
            "action": _ACTION.value,
            "assignment_uuid": str(assignment_uuid),
            "event_action": event_action,
            "living_organism_id": str(organism.organism_id),
            "resulting_state": target_state,
            "schema": _TARGET_SCHEMA,
        }
        if assign_term is not None:
            target["effective_until"] = (
                None if assign_term[1] is None else canonical_timestamp(assign_term[1])
            )
            target["effective_until_state"] = assign_term[0]
            target["suspension_tolls_term"] = _TOLLING_VALUE
            target["suspension_tolls_term_source"] = _TOLLING_SOURCE
        target_fingerprint = authority_target_fingerprint(target)
        command_payload: dict[str, object] = {
            "action": event_action,
            "assignment_uuid": str(assignment_uuid),
            "effective_at": canonical_timestamp(effective_at),
            "received_at": canonical_timestamp(received_at),
            "resulting_state": target_state,
            "schema": _COMMAND_SCHEMA,
        }
        if assign_term is not None:
            command_payload["effective_until"] = (
                None if assign_term[1] is None else canonical_timestamp(assign_term[1])
            )
            command_payload["effective_until_state"] = assign_term[0]
            command_payload["suspension_tolls_term"] = _TOLLING_VALUE
            command_payload["suspension_tolls_term_source"] = _TOLLING_SOURCE
        command_fingerprint = self._command_fingerprint(
            actor=actor,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            target=target,
            command=command_payload,
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

        if (
            event_action in {"ASSIGN", "REASSIGN"}
            or (event_action == "END" and assignment.membership.identity.identity_id != actor.identity_id)
        ):
            _read_actor_active_membership(
                database_alias=self._alias,
                actor_identity_pk=actor.pk,
                living_organism_pk=assignment.membership.living_organism_id,
                unavailable=S015AssignmentAuthorityUnavailable,
            )
        if event_action == "ASSIGN":
            organism = (
                LivingOrganism.objects.using(self._alias)
                .select_for_update()
                .get(pk=assignment.membership.living_organism_id)
            )
        else:
            _lock_registered_organism(
                database_alias=self._alias,
                organism_id=assignment.membership.living_organism.organism_id,
            )
            assignment = (
                ContextualRoleAssignment.objects.using(self._alias)
                .select_for_update(of=("self", "membership"))
                .select_related("membership__living_organism", "membership__identity", "role_definition")
                .get(pk=assignment.pk)
            )
            organism = assignment.membership.living_organism
        actor_membership, actor_role_refs = _lock_actor_membership_context(
            database_alias=self._alias,
            actor_identity_id=actor.identity_id,
            living_organism_id=organism.organism_id,
            unavailable=S015AssignmentAuthorityUnavailable,
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
            target=target,
            living_organism_id=organism.organism_id,
            assignment_uuid=assignment_uuid,
        )

        if event_action == "ASSIGN":
            assignment = (
                ContextualRoleAssignment.objects.using(self._alias)
                .select_for_update()
                .select_related("membership", "role_definition")
                .get(pk=assignment.pk)
            )
            # R4 slice 2 (the Human Governor, 4 Oct 2026): a completed departure is honoured at
            # record time. The nominee's membership state is read from the row locked above,
            # under the organism lock, after definitive authorisation. Neither the provider's
            # fold at the act's coordinates nor a supplied earlier effective_at overrides it:
            # an office requires a current membership (⟨C.037⟩). Mirrors _execute_propose.
            if assignment.membership.state != "ACTIVE":
                raise S015AssignmentMembershipRefused(
                    "an office requires a current membership (⟨C.037⟩)"
                )
            proposal_recorded_at = (
                ContextualRoleAssignmentStateEvent.objects.using(self._alias)
                .filter(assignment=assignment, sequence=1, action="PROPOSE")
                .values_list("recorded_at", flat=True)
                .first()
            )
            if proposal_recorded_at is None:
                raise S015AssignmentNotFound("office nomination proposal not found")
            if OrganismMembershipTransition.objects.using(self._alias).filter(
                membership_id=assignment.membership_id,
                resulting_state="ENDED",
                recorded_at__gt=proposal_recorded_at,
            ).exists():
                raise S015AssignmentMembershipRefused(
                    "the nomination predates the nominee's departure; a fresh nomination is required"
                )
            self._verify_assign_consent(
                assignment=assignment,
                effective_at=effective_at,
                authority_reference=decision.authority_reference,
            )

        # Every refusal that reads the target's state sits after authority (the d6af911 order).
        # IL-6 and IL-8 bind every act on an office, not only its proposal (the Reviewer's S2 F2):
        # scope before code, as in _execute_propose
        role_definition = assignment.role_definition
        if role_definition.scope != OrganismRoleDefinition.Scope.LIVING_ORGANISM:
            raise S015OperationNotYetImplemented("circle_scoped_office", "IL-6", "S4")
        if role_definition.code != _COORDINATOR_ROLE_CODE:
            raise S015OperationNotYetImplemented("transition_non_coordinator_office", "IL-8", "SR")
        current_state = _canonical_key(assignment.state, "assignment.state", 19)
        _validate_assignment_edge(
            current_state=current_state,
            action=event_action,
            target_state=target_state,
        )
        head_event = None
        if assignment.head_state_event_id is not None:
            head_event = (
                ContextualRoleAssignmentStateEvent.objects.using(self._alias)
                .only("sequence", "effective_at", "occurred_at", "received_at")
                .get(pk=assignment.head_state_event_id)
            )
        if head_event is None:
            raise S015AssignmentNotFound("assignment head event not found")
        if (effective_at, occurred_at, received_at) < (
            head_event.effective_at,
            head_event.occurred_at,
            head_event.received_at,
        ):
            raise S015OperationNotYetImplemented("out_of_order_assignment_event", "IL-3", "S5")
        if event_action in {"END", "REASSIGN"}:
            founder_identity_id = organism.founder_identity_id
            if founder_identity_id is not None and actor.pk != founder_identity_id:
                if _founding_provenance_assignment_id(
                    database_alias=self._alias,
                    assignment_id=assignment.pk,
                    living_organism_id=organism.pk,
                ) is not None:
                    from src.intevia.services.organism_application_authority import (
                        ProductionBirthingFactSource,
                    )

                    birthing_reference = ProductionBirthingFactSource(
                        database_alias=self._alias,
                    ).reference_at(
                        organism.organism_id,
                        effective_at,
                        received_at,
                    )
                    if birthing_reference is None:
                        raise S015AuthorityDenied(
                            _intake_tail(
                                "The office was not ended or reassigned; no change was made to the office. "
                                "Until this LO is born, the founder's original Coordinator appointment can "
                                "be ended or reassigned only by the founder. This is a governed protection "
                                "of the LO's founding, not a technical fault. After Birthing it no longer "
                                "applies: the appointment can then be ended or reassigned under the LO's "
                                "ordinary governed rules. INTEVIA kept a record that this request was "
                                "received at {received_at}; sent again unchanged, it is answered as of "
                                "that time.",
                                received_at,
                            ),
                            refusal_code=S015AuthorityRefusalCode.C_FOUNDER_PROTECTED,
                            disclosed_refusal_code=S015AuthorityRefusalCode.C_FOUNDER_PROTECTED,
                        )

        basis_id = organism.founding_authority_basis_id
        if basis_id is None:
            raise S015AssignmentAuthorityUnavailable(
                "organism founding authority basis is required"
            )
        basis = (
            AuthorityBasis.objects.using(self._alias)
            .select_for_update()
            .get(pk=basis_id)
        )

        if event_action == "ASSIGN":
            write_until_state, write_until = assign_term
        else:
            write_until_state, write_until = "NOT_ESTABLISHED", None
        event_id = self._write_event(
            assignment=assignment,
            actor=actor,
            sequence=head_event.sequence + 1,
            predecessor=head_event,
            event_action=event_action,
            effective_until=write_until,
            effective_until_state=write_until_state,
            decision=decision,
            basis_id=basis.pk,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=command_fingerprint,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            prior_state=current_state,
            resulting_state=target_state,
        )
        result = self._refresh_and_result(
            assignment=assignment,
            event_id=event_id,
            effective_until=write_until,
            effective_until_state=write_until_state,
            with_tolling=event_action == "ASSIGN",
            decision=decision,
            effective_at=effective_at,
        )
        return self._finalise(
            actor=actor,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=command_fingerprint,
            result=result,
            received_at=bound.received_at,
        )

    def _verify_assign_consent(
        self,
        *,
        assignment: ContextualRoleAssignment,
        effective_at: datetime,
        authority_reference: str,
    ) -> None:
        # RC-1 commission v1.3, item 4 (Human Governor-approved text (b)).
        """Check ASSIGN's authority reference against the nominee's consent.

        Called by _execute_transition after authority qualification and its
        nominee-membership checks. This helper does not itself qualify authority.

        Raises S015AssignmentNotFound if the assignment's sequence-1 PROPOSE
        event is missing. Reads clock_timestamp() from the database as the
        knowledge bound for consent records.

        For a self-nomination, where the proposal's actor is the nominee, the
        expected reference is "e:<proposal_event_uuid>" unless a WITHDRAW for
        this assignment and proposal has effective_at no later than the supplied
        effective_at and recorded_at no later than the knowledge bound.

        For another person's nomination, considers consent records for this
        assignment, proposal and nominee membership within those same time
        bounds. The record with the greatest ordinal supplies
        "c:<consent_uuid>" only if its action is CONSENT. No record, or any
        other latest action, leaves no expected reference.

        The authority evidence must be readable: a reference whose type is not
        exactly str, lacks the "s015a1:" prefix, is not valid JSON after it, or
        does not decode to an object raises S015AssignmentEdgeRefused with the
        Human Governor's record-fault text (b) (RC-1, item 4). A readable
        reference then raises S015AssignmentEdgeRefused with the case-specific
        message if no expected reference exists or its "u" value differs from
        the expected one.

        This helper only reads records and raises refusals; it writes no
        appointment or receipt. The caller has already qualified authority.
        It is the surrounding transaction, not this helper alone, that governs
        rollback of any earlier work when this exception propagates.
        """
        record_fault_message = (
            "Appointment not completed: the recorded authority evidence for this appointment could not be read. "
            "No appointment was made by this attempt. This is a record fault, not a withdrawal or decline by "
            "the nominee; it needs to be checked before proceeding."
        )
        proposal = ContextualRoleAssignmentStateEvent.objects.using(self._alias).filter(
            assignment=assignment,
            sequence=1,
            action="PROPOSE",
        ).first()
        if proposal is None:
            raise S015AssignmentNotFound("office nomination proposal not found")
        with connections[self._alias].cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            record_time = cursor.fetchone()[0]
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT actor_id FROM public.core_contextualroleassignmentstateevent WHERE id = %s",
                [proposal.pk],
            )
            proposal_actor_id, = cursor.fetchone()
        if proposal_actor_id == assignment.membership.identity_id:
            withdrawn = OfficeAppointmentConsent.objects.using(self._alias).filter(
                assignment=assignment,
                proposal_event=proposal,
                action="WITHDRAW",
                effective_at__lte=effective_at,
                recorded_at__lte=record_time,
            ).exists()
            expected_reference = None if withdrawn else "e:" + str(proposal.event_uuid)
            refusal_message = (
                "Appointment not completed: the nominee withdrew their consent to this nomination. "
                "This nomination is closed. If they wish to proceed later, a fresh nomination is required."
                if withdrawn
                else (
                    "Appointment not completed: the recorded authority reference does not match the "
                    "nominee's current consent record. No appointment was made. This is a record "
                    "inconsistency, not a withdrawal or decline by the nominee. The inconsistency "
                    "needs to be checked before proceeding."
                )
            )
        else:
            rows = list(OfficeAppointmentConsent.objects.using(self._alias).filter(
                assignment=assignment,
                proposal_event=proposal,
                nominee_membership=assignment.membership,
                effective_at__lte=effective_at,
                recorded_at__lte=record_time,
            ).order_by("ordinal"))
            latest = rows[-1] if rows else None
            if latest is None:
                refusal_message = (
                    "Appointment not completed: the nominee has not yet consented to this nomination. "
                    "No appointment was made. The appointment can proceed once they record their consent."
                )
            elif latest.action == "DECLINE":
                refusal_message = (
                    "Appointment not completed: the nominee declined this nomination. "
                    "This nomination is closed. If they wish to proceed later, a fresh nomination is required."
                )
            elif latest.action == "WITHDRAW":
                refusal_message = (
                    "Appointment not completed: the nominee withdrew their consent to this nomination. "
                    "This nomination is closed. If they wish to proceed later, a fresh nomination is required."
                )
            else:
                refusal_message = (
                    "Appointment not completed: the recorded authority reference does not match the "
                    "nominee's current consent record. No appointment was made. This is a record "
                    "inconsistency, not a withdrawal or decline by the nominee. The inconsistency "
                    "needs to be checked before proceeding."
                )
            expected_reference = (
                "c:" + str(latest.consent_uuid)
                if latest is not None and latest.action == "CONSENT"
                else None
            )
        if type(authority_reference) is not str or not authority_reference.startswith("s015a1:"):
            raise S015AssignmentEdgeRefused(record_fault_message)
        try:
            facts = json.loads(authority_reference[len("s015a1:"):])
        except (TypeError, ValueError) as exc:
            raise S015AssignmentEdgeRefused(record_fault_message) from exc
        if type(facts) is not dict:
            raise S015AssignmentEdgeRefused(record_fault_message) from TypeError(
                "authority evidence facts must be a dict"
            )
        if expected_reference is None or facts.get("u") != expected_reference:
            raise S015AssignmentEdgeRefused(refusal_message)

    def _write_event(
        self,
        *,
        assignment: ContextualRoleAssignment,
        actor: Identity,
        sequence: int,
        predecessor: ContextualRoleAssignmentStateEvent | None,
        event_action: str,
        effective_until: datetime | None,
        effective_until_state: str,
        decision,
        basis_id: int,
        request_reference: str,
        idempotency_key: str,
        payload_fingerprint: str,
        occurred_at: datetime,
        effective_at: datetime,
        received_at: datetime,
        prior_state: str | None,
        resulting_state: str,
    ) -> int:
        """Write one assignment state event and return its id.

        For ASSIGN, END and REASSIGN, records actor_state LIVING_ORGANISM
        with actor_id and actor_access_epoch set to None. Other actions,
        including PROPOSE, record actor_state PARTY with the supplied actor
        Identity's id and access epoch.

        In both cases, records the supplied actor Identity as the enterer,
        entry_mode TRANSPORT and actor_capacity STANDING. Sets composing-rule
        and composer states to NA_TRANSPORT, with their references unset.

        Derives the temporal basis through _resulting_temporal_basis. Builds
        an "s015l1:" lineage reference by hashing the lineage schema bytes
        followed by the canonical JSON of the selected command fields. Those
        fields include the supplied actor Identity's UUID and access epoch,
        including for an LO act.

        Passes the event values, authority basis, authority-decision reference,
        authority reference as evidence, and term fields to
        _insert_assignment_event_row. Supplies None for l1_commitment and
        l2_commitment; this method does not compute them.
        """
        temporal_basis_kind, temporal_basis_reference = _resulting_temporal_basis(
            payload_fingerprint=payload_fingerprint,
            effective_at=effective_at,
            received_at=received_at,
        )
        living_organism_act = event_action in {"ASSIGN", "END", "REASSIGN"}
        lineage_reference = f"s015l1:{hashlib.sha256(_LINEAGE_SCHEMA.encode('utf-8') + canonical_json_bytes({
            'action': _ACTION.value,
            'actor_access_epoch': actor.access_epoch,
            'actor_identity_id': str(actor.identity_id),
            'assignment_uuid': str(assignment.assignment_uuid),
            'effective_at': canonical_timestamp(effective_at),
            'event_action': event_action,
            'occurred_at': canonical_timestamp(occurred_at),
            'payload_fingerprint': payload_fingerprint,
            'received_at': canonical_timestamp(received_at),
            'resulting_state': resulting_state,
            'schema': _LINEAGE_SCHEMA,
            'sequence': sequence,
        })).hexdigest()}"
        return _insert_assignment_event_row(
            database_alias=self._alias,
            event_uuid=uuid4(),
            assignment_id=assignment.pk,
            sequence=sequence,
            predecessor_id=predecessor.pk if predecessor is not None else None,
            predecessor_sequence=predecessor.sequence if predecessor is not None else None,
            action=event_action,
            actor_id=None if living_organism_act else actor.pk,
            actor_state="LIVING_ORGANISM" if living_organism_act else "PARTY",
            actor_capacity="STANDING",
            enterer_identity_id=actor.pk,
            entry_mode="TRANSPORT",
            composing_rule_state="NA_TRANSPORT",
            composing_rule_reference=None,
            composer_state="NA_TRANSPORT",
            composer_identity_id=None,
            effective_until=effective_until,
            effective_until_state=effective_until_state,
            l1_commitment=None,
            l2_commitment=None,
            actor_access_epoch=None if living_organism_act else actor.access_epoch,
            authority_basis_id=basis_id,
            authority_decision_reference=decision.authority_decision_reference,
            evidence_reference=decision.authority_reference,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=payload_fingerprint,
            lineage_reference=lineage_reference,
            temporal_basis_kind=temporal_basis_kind,
            temporal_basis_reference=temporal_basis_reference,
            occurred_at=occurred_at,
            effective_at=effective_at,
            received_at=received_at,
            prior_state=prior_state,
            resulting_state=resulting_state,
        )

    def _refresh_and_result(
        self,
        *,
        assignment: ContextualRoleAssignment,
        event_id: int,
        effective_until: datetime | None,
        effective_until_state: str,
        with_tolling: bool,
        decision,
        effective_at: datetime,
    ) -> AssignmentCommandResult:
        """Refresh the assignment's governed cache and build the command result.

        Calls s015_refresh_governed_cache for the assignment at effective_at,
        with the database clock (clock_timestamp) as the knowledge time, reloads
        the assignment and the new event, and builds the result from them: the
        event's state, sequence, action, lineage and temporal basis; the
        assignment's state coordinates; the supplied term; the default
        no-tolling value and source only when with_tolling is set; and the
        authority and authority-decision references.
        """
        with connections[self._alias].cursor() as cursor:
            cursor.execute(
                "SELECT public.s015_refresh_governed_cache(%s, %s, %s, clock_timestamp())",
                [_ANCHOR_TOKEN, assignment.pk, effective_at],
            )
        assignment.refresh_from_db(using=self._alias)
        event = ContextualRoleAssignmentStateEvent.objects.using(self._alias).get(pk=event_id)
        event.refresh_from_db(using=self._alias)
        return AssignmentCommandResult(
            assignment_uuid=assignment.assignment_uuid,
            resulting_state=event.resulting_state,
            event_uuid=event.event_uuid,
            event_sequence=event.sequence,
            event_action=event.action,
            lineage_reference=event.lineage_reference,
            temporal_basis_kind=event.temporal_basis_kind,
            temporal_basis_reference=event.temporal_basis_reference,
            effective_until=effective_until,
            effective_until_state=effective_until_state,
            suspension_tolls_term=_TOLLING_VALUE if with_tolling else None,
            suspension_tolls_term_source=_TOLLING_SOURCE if with_tolling else None,
            current_state_effective_at=assignment.current_state_effective_at,
            state_known_at=assignment.state_known_at,
            state_source_event_set_fingerprint=assignment.state_source_event_set_fingerprint,
            authority_reference=decision.authority_reference,
            authority_decision_reference=decision.authority_decision_reference,
        )


__all__ = [
    "AssignmentCommandResult",
    "OrganismRoleService",
    "ProposeAssignmentCommand",
    "S015AssignmentAuthorityUnavailable",
    "S015AssignmentCommandError",
    "S015AssignmentEdgeRefused",
    "S015AssignmentMembershipRefused",
    "S015AssignmentNotFound",
    "S015AssignmentStateUnused",
    "S015AssignmentTermRefused",
    "S015CommandAtomicityRequired",
    "S015CommandMutexUnavailable",
    "S015CommandReceiptMalformed",
    "S015CommandReplayMismatch",
    "S015CommandSubstrateError",
    "S015OperationNotYetImplemented",
    "TransitionAssignmentCommand",
    "acquire_organism_command_mutex",
    "assignment_in_effect",
    "command_mutex_key",
]
