"""PKT-B slice S2 — office appointments and authorised endings (contextual role assignments).

Commission: COPILOT_COMMISSION_PKT_B_S2_v1_1.md, carrying the Human Governor's rulings of
29 September 2026. Design: docs/s015/pkt_b/PKT_B_S2_DESIGN_EXTRACT_v1_0.md (⟨B.050⟩, ⟨C.064⟩,
⟨E2.130⟩, ⟨E1.038⟩, ⟨C.051⟩, ⟨C.023⟩, ⟨E1.016⟩–⟨E1.019⟩, ⟨U5.010⟩), with the S1 extract
(⟨B.049⟩, ⟨C.037⟩–⟨C.040⟩, ⟨E2.114⟩).

Commands: `propose_assignment` (the office anchor plus its sequence-1 PROPOSE event) and
`transition_assignment` (the decided edges of ⟨B.050⟩: PROPOSED → ACTIVE by ASSIGN — the
appointment, an explicit act (K-3) — and ACTIVE → ENDED by END or REASSIGN; a fresh appointment
is a new anchor through `propose_assignment`, U21g-6). Plus the read
`assignment_in_effect(assignment, at)`: the recorded state stays ACTIVE until an ending is
recorded; a FIXED term reads not-in-effect at and after `effective_until` (no tolling under the
default, ⟨E2.130⟩).

The term and the no-tolling snapshot are appointment content (commission §3.3): the appointing
ASSIGN command supplies `effective_until_state` / `effective_until` (required for ASSIGN, refused
for END and REASSIGN), validated against the landed 0022 checks and bound into ASSIGN's target and
payload fingerprints so the qualified decision covers them, then written on the ASSIGN event's own
columns. The proposal may carry its proposed term as proposal content in its own fingerprints;
nothing reads it back, and a chain row's effective_until states when that event's effect ends, so
the PROPOSE row itself carries NOT_ESTABLISHED / NULL. `suspension_tolls_term = NO` (source:
`DEFAULT (no settings store)` — no settings store is landed; HD-E6 is open) is snapshotted with
the appointment and bound likewise, and readably in the receipt. The office event chain has no
`reason_class` column — the action token records the kind of act — and no landed column can hold
the tolling value readably (`core_governedeventcontent` is unusable: the L2 canonical form is
undefined, U-14). That dependency (a readable snapshot home, with HD-E6) is declared, not patched
round.

Order inside the transaction (as S1 at d6af911): the atomic block; canonical input validation
(including refusal of an undecided or unused action or state token); the actor lock; the mutex;
target resolution so the fingerprint can be built (a missing target is not-found); replay; then
authority; then every state-reading refusal — the nominee's current membership on ASSIGN (R4
slice 2), IL-4, the decided edge set, IL-3, PE-04 — then the
event write with every chain-contract column (TRANSPORT, actor PARTY/STANDING, enterer = actor),
the cache refresh with `clock_timestamp()`, and the receipt.

Implementation limits in force — temporary refusals, not office or membership policy:
- IL-2 (S5, after the rule home, with HD-C5): a membership departure while an ACTIVE office is
  held. S1's code names it; S2 changes nothing in S1. The departure's office endings are
  rule-composed consequences (actor NONE, the Ruling 4 rule reference — ⟨C.023⟩ row 7, ⟨C.051⟩);
  no genuine rule reference exists in landed data (`core_governedrule` / B-G3 are proposed, not
  landed), so none is written, and the departure's actor is never substituted.
- IL-3 (S5): an out-of-order office event (`effective_at, occurred_at, received_at` before the
  head's), refused after authority, as in S1.
- IL-4 (S2b, after HD-E6): ACTIVE ⇄ SUSPENDED role suspension — needs the
  `role_suspension_enabled` setting (no settings store is landed; HD-E6 open) and the constitutional-instrument reference (⟨E1.038⟩).
- IL-5 (S5, after the rule home): the writer that records a term's end — a scheduled,
  rule-composed act (actor NONE, the term-end rule). No command is exposed for it in S2.
  Effective expiry is a read rule (`assignment_in_effect`); the ending is a record.
- IL-6 (S4): a Circle-scoped office (`circle` not NULL, or a CIRCLE-scoped role definition); refused on propose and on every transition.
- PE-04 (SB — the Birthing slice): an END or REASSIGN of only the founder's original LO-scope
  COORDINATOR appointment is refused for a non-founder enterer only while no Birthing event is
  eligible at the attempted act's coordinates. The founder's own ending, later appointments,
  non-founder appointments and Circle-scope appointments are not protected by PE-04. Provenance
  is established by the founding-transaction test or the durable record rule in
  `_founding_appointment_by_record`; the latter reads no transaction ids or timestamps.
- IL-8 (SR): appointment to, or any transition of, an office whose role definition is not COORDINATOR. The landed schema lets an
  organism define offices of its own, beyond COORDINATOR and the root's reserved five (which exist
  only on the platform root, `s015_platform_root_unset_ck` shipping FALSE-only, D-39); SR's row
  covers the reserved offices and organism-defined offices alike. The check is an exact code
  comparison, not a prefix.

The replay substrate (`qualify_completed_replay` / `QualifiedOrganismCommandReplay`) lives in
src/intevia/services/organism_membership_service.py and is untouched (the Reviewer's F1).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
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
    S015CommandReplayMismatch,
    S015CommandSubstrateError,
    _canonical_key,
    _canonical_uuid,
    _command_payload,
    _empty_anchor_fingerprint,
    _founding_provenance_assignment_id,
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
    C_FOUNDER_PROTECTED_MESSAGE,
    S015AuthorityDenied,
    S015AuthorityRefusalCode,
)


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
    pass


class S015AssignmentAuthorityUnavailable(S015AssignmentCommandError):
    pass


class S015AssignmentNotFound(S015AssignmentCommandError):
    pass


class S015AssignmentEdgeRefused(S015AssignmentCommandError):
    pass


class S015AssignmentStateUnused(S015AssignmentCommandError):
    pass


class S015AssignmentTermRefused(S015AssignmentCommandError):
    pass


class S015AssignmentMembershipRefused(S015AssignmentCommandError):
    pass


@dataclass(frozen=True, slots=True)
class ProposeAssignmentCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime
    membership_uuid: UUID
    role_definition_uuid: UUID
    effective_until_state: str
    effective_until: datetime | None


@dataclass(frozen=True, slots=True)
class TransitionAssignmentCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    effective_at: datetime
    received_at: datetime
    assignment_uuid: UUID
    action: str
    resulting_state: str
    # appointment content the appointing decision supplies (§3.3): required for ASSIGN,
    # refused for END and REASSIGN
    effective_until_state: str | None = None
    effective_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class AssignmentCommandResult:
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
        cls, payload: object, *, database_alias: str = "default",
    ) -> "AssignmentCommandResult":
        if type(payload) is not dict:
            raise S015CommandReplayMismatch("completed receipt does not match request")
        try:
            effective_until_raw = payload["effective_until"]
            event_uuid_raw = payload.get("event_uuid")
            if event_uuid_raw is None:
                event_uuid_raw = ContextualRoleAssignmentStateEvent.objects.using(database_alias).filter(
                    assignment__assignment_uuid=UUID(payload["assignment_uuid"]),
                    sequence=int(payload["event_sequence"]),
                    action=str(payload["event_action"]),
                ).values_list("event_uuid", flat=True).first()
            if event_uuid_raw is None:
                raise ValueError("event uuid is unavailable")
            return cls(
                assignment_uuid=UUID(payload["assignment_uuid"]),
                resulting_state=str(payload["resulting_state"]),
                event_uuid=UUID(event_uuid_raw),
                event_sequence=int(payload["event_sequence"]),
                event_action=str(payload["event_action"]),
                lineage_reference=str(payload["lineage_reference"]),
                temporal_basis_kind=payload.get("temporal_basis_kind"),
                temporal_basis_reference=payload.get("temporal_basis_reference"),
                effective_until=(
                    None if effective_until_raw is None else _parse_canonical_timestamp(effective_until_raw)
                ),
                effective_until_state=str(payload["effective_until_state"]),
                suspension_tolls_term=payload.get("suspension_tolls_term"),
                suspension_tolls_term_source=payload.get("suspension_tolls_term_source"),
                current_state_effective_at=_parse_canonical_timestamp(payload["current_state_effective_at"]),
                state_known_at=_parse_canonical_timestamp(payload["state_known_at"]),
                state_source_event_set_fingerprint=str(payload["state_source_event_set_fingerprint"]),
                authority_reference=str(payload["authority_reference"]),
                authority_decision_reference=str(payload["authority_decision_reference"]),
            )
        except Exception as exc:  # pragma: no cover - defensive
            raise S015CommandReplayMismatch("completed receipt does not match request") from exc


def _parse_canonical_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise TypeError("timestamp payload must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate_term(
    *,
    effective_until_state: object,
    effective_until: object,
    effective_at: datetime,
) -> tuple[str, datetime | None]:
    """The landed term vocabulary (0022 until_state_vocab_ck/until_value_ck/until_after_from_ck),
    refused at input canonicalisation, before any write."""
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
    state = _canonical_key(value, "resulting_state", 19)
    if state in _STATE_UNUSED:
        raise S015AssignmentStateUnused(
            f"assignment state {state} is unused by v1.0 (⟨B.050⟩)"
        )
    if state not in _STATE_VOCABULARY:
        raise S015AssignmentEdgeRefused(f"assignment state {state} is not decided")
    return state


def _validate_event_action(value: object) -> str:
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
    """The decided edge set (⟨B.050⟩) and IL-4, after authority. PROPOSED -> ENDED, every
    PROBATIONARY edge and every other edge are refused without a write."""
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
) -> int:
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
    """The term the appointing decision supplied, snapshotted on the ASSIGN event, if one is
    recorded."""
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
    """Return the cached state subject to the recorded fixed-term expiry rule."""
    if type(assignment) is not ContextualRoleAssignment:
        raise TypeError("assignment must be a ContextualRoleAssignment")
    moment = _validate_timestamp(at, "at")
    term = _head_assign_term(database_alias=database_alias, assignment_id=assignment.pk)
    if term is not None and term[0] == "FIXED" and term[1] is not None and moment >= term[1]:
        return False
    return assignment.state == "ACTIVE"


class OrganismRoleService(_OrganismCommandService):
    _action = _ACTION
    _not_found = S015AssignmentNotFound
    _authority_unavailable = S015AssignmentAuthorityUnavailable

    def _replay_result(self, payload: object) -> AssignmentCommandResult:
        return AssignmentCommandResult.from_receipt_payload(payload, database_alias=self._alias)

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

    def propose_assignment(self, command: ProposeAssignmentCommand) -> AssignmentCommandResult:
        with authority_atomic(self._alias):
            return self._execute_propose(command)

    def transition_assignment(self, command: TransitionAssignmentCommand) -> AssignmentCommandResult:
        with authority_atomic(self._alias):
            return self._execute_transition(command)

    def assignment_in_effect(self, assignment: ContextualRoleAssignment, at: datetime) -> bool:
        """A read, not a record: False when the appointment's term is FIXED and `at` is at or
        after `effective_until` (no tolling under the default), otherwise the cached state's
        reading. The recorded state stays ACTIVE until an ending is recorded (IL-5 is unbuilt)."""
        return assignment_in_effect(assignment, at, database_alias=self._alias)

    def open_nominations(
        self,
        living_organism_uuid: UUID,
        *,
        effective_at: datetime,
        known_at: datetime,
    ) -> list[ContextualRoleAssignment]:
        """Return PROPOSED offices not closed by a terminal nominee act at these coordinates."""
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

    def _execute_propose(self, command: ProposeAssignmentCommand) -> AssignmentCommandResult:
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(command.received_at, "received_at")
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
        )

    def _execute_transition(self, command: TransitionAssignmentCommand) -> AssignmentCommandResult:
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        received_at = _validate_timestamp(command.received_at, "received_at")
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
                            C_FOUNDER_PROTECTED_MESSAGE,
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
        )

    def _verify_assign_consent(
        self,
        *,
        assignment: ContextualRoleAssignment,
        effective_at: datetime,
        authority_reference: str,
    ) -> None:
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
        try:
            facts = json.loads(authority_reference[len("s015a1:"):])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise S015AssignmentEdgeRefused("ASSIGN authority did not name its consent fact") from exc
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
    "S015CommandReplayMismatch",
    "S015CommandSubstrateError",
    "S015OperationNotYetImplemented",
    "TransitionAssignmentCommand",
    "acquire_organism_command_mutex",
    "assignment_in_effect",
    "command_mutex_key",
]
