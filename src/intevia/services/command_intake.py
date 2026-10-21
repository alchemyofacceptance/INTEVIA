"""Durable, inactive command-intake boundary for S015 governed requests."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, replace
from datetime import datetime
from enum import Enum
import hashlib
from typing import Callable, Iterable
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError, connections, transaction

from core.models import Identity, IntakeEntry
from core.organism_contract import OrganismAction
from src.intevia.services.service_authority import (
    canonical_json_bytes,
    canonical_timestamp,
)
from src.intevia.services.organism_command_support import (
    S015CommandAtomicityRequired,
    S015CommandContended,
    S015CommandMutexUnavailable,
    S015CommandReplayMismatch,
    _canonical_key,
    _canonical_uuid,
    _validate_timestamp,
)


DOMAIN_INTAKE = b"INTEVIA:S015:COMMAND_INTAKE:v1\x00"
FINGERPRINT_SCHEMA = "s015-intake-v1"
INSERT_COMMAND_INTAKE_SQL = """
INSERT INTO public.core_commandintake (
    actor_id, actor_identity_id, actor_access_epoch, entry, action,
    idempotency_key, request_reference, request_fingerprint,
    fingerprint_schema, target_organism_id
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (actor_id, action, idempotency_key) DO NOTHING
RETURNING id, received_at
"""
COMPOUND_ENTRIES = (
    IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
    IntakeEntry.SEAT_GOVERNANCE_CIRCLE,
)
_CONSTRUCTOR_SENTINEL = object()
_COMPOUND_ENTRY_VALUES = tuple(entry.value for entry in COMPOUND_ENTRIES)


class ChildEntry(str, Enum):
    BIRTHING_ELECTION = "BIRTHING_ELECTION"
    AUTOMATIC_ADMISSION = "AUTOMATIC_ADMISSION"
    REQUEST_STAGE = "REQUEST_STAGE"
    ADMIT_STAGE = "ADMIT_STAGE"


_CHILD_PUBLIC_ENTRIES = {
    ChildEntry.REQUEST_STAGE: IntakeEntry.REQUEST_GOVERNANCE_SEAT,
    ChildEntry.ADMIT_STAGE: IntakeEntry.DECIDE_GOVERNANCE_SEAT,
}
_CHILD_ACTIONS = {
    ChildEntry.REQUEST_STAGE: OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
    ChildEntry.ADMIT_STAGE: OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
}
_COMPOUND_CHILDREN = frozenset(
    (parent, child)
    for parent in COMPOUND_ENTRIES
    for child in (ChildEntry.REQUEST_STAGE, ChildEntry.ADMIT_STAGE)
)


def _entry_is_served(
    expected: IntakeEntry | frozenset[IntakeEntry | ChildEntry],
    actual: IntakeEntry | ChildEntry,
) -> bool:
    """Check an exact legacy entry or membership in an executor's served set."""
    if type(expected) is IntakeEntry:
        return type(actual) is IntakeEntry and actual is expected
    return type(expected) is frozenset and actual in expected


def _require_verified_compound_parent(bound: BoundRequest) -> None:
    """Require a content-verified parent and an allowed compound-stage pair."""
    if (
        bound.parent is None
        or not bound.parent.content_verified
        or (bound.parent.entry, bound.entry) not in _COMPOUND_CHILDREN
    ):
        raise _request_binding_mismatch()


@dataclass(frozen=True, slots=True)
class ActorRef:
    pk: int
    identity_id: UUID
    access_epoch: int


@dataclass(frozen=True, slots=True)
class IntakeShape:
    idempotency_key: str
    request_reference: str
    target: dict[str, object]
    command: dict[str, object]
    omitted: tuple[str, ...]
    target_organism_id: UUID | None
    action: str | None = None


@dataclass(frozen=True, slots=True)
class BoundRequest:
    entry: IntakeEntry | ChildEntry
    action: str
    idempotency_key: str
    request_reference: str
    request_fingerprint: str
    actor_pk: int
    actor_identity_id: UUID
    actor_access_epoch: int
    received_at: datetime
    intake_id: int | None
    target_organism_id: UUID | None
    first: bool
    parent: BoundRequest | None
    _sentinel: InitVar[object]
    content_verified: bool = False

    def __post_init__(self, _sentinel: object) -> None:
        if _sentinel is not _CONSTRUCTOR_SENTINEL:
            raise TypeError("BoundRequest must be created by the intake boundary")

    @classmethod
    def for_test(
        cls,
        entry: IntakeEntry,
        command: object,
        *,
        received_at: datetime,
        actor: ActorRef,
    ) -> BoundRequest:
        if not getattr(settings, "INTEVIA_TEST_CONTEXT", False):
            raise IntakeAdmissionTechnicalFailure(
                "test-only bound requests require INTEVIA_TEST_CONTEXT"
            )
        shape = intake_shape(entry, command)
        action = _action_for(entry, shape.action)
        fingerprint = intake_fingerprint(
            entry=entry,
            action=action,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            request_reference=shape.request_reference,
            idempotency_key=shape.idempotency_key,
            target=shape.target,
            command=shape.command,
            omitted=shape.omitted,
        )
        return cls(
            entry=entry,
            action=action,
            idempotency_key=shape.idempotency_key,
            request_reference=shape.request_reference,
            request_fingerprint=fingerprint,
            actor_pk=actor.pk,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            received_at=_validate_timestamp(received_at, "received_at"),
            intake_id=None,
            target_organism_id=shape.target_organism_id,
            first=False,
            parent=None,
            _sentinel=_CONSTRUCTOR_SENTINEL,
        )

    def verify_content(
        self,
        command: object,
        entry: IntakeEntry | frozenset[IntakeEntry | ChildEntry],
    ) -> BoundRequest:
        """Verify command content and return an immutable content-verified copy."""
        try:
            if not _entry_is_served(entry, self.entry):
                raise _request_binding_mismatch()
            if isinstance(self.entry, ChildEntry):
                _require_verified_compound_parent(self)
                shape_entry = _CHILD_PUBLIC_ENTRIES.get(self.entry)
                if shape_entry is None:
                    raise _request_binding_mismatch()
            else:
                if self.parent is not None:
                    raise _request_binding_mismatch()
                shape_entry = self.entry
            shape = intake_shape(shape_entry, command)
            action = _action_for(shape_entry, shape.action)
            fingerprint = intake_fingerprint(
                entry=self.entry,
                action=action,
                actor_identity_id=self.actor_identity_id,
                actor_access_epoch=self.actor_access_epoch,
                request_reference=shape.request_reference,
                idempotency_key=shape.idempotency_key,
                target=shape.target,
                command=shape.command,
                omitted=shape.omitted,
            )
        except (TypeError, ValueError) as exc:
            raise _request_binding_mismatch() from exc
        if (
            self.action != action
            or self.idempotency_key != shape.idempotency_key
            or self.request_reference != shape.request_reference
            or self.request_fingerprint != fingerprint
        ):
            raise _request_binding_mismatch()
        if self.intake_id is None and not getattr(settings, "INTEVIA_TEST_CONTEXT", False):
            raise _request_binding_mismatch()
        return replace(
            self,
            content_verified=True,
            _sentinel=_CONSTRUCTOR_SENTINEL,
        )

    def verify(
        self,
        command: object,
        actor: Identity,
        entry: IntakeEntry | frozenset[IntakeEntry | ChildEntry],
        alias: str,
    ) -> None:
        """Verify actor, epoch and the committed parent row after content checks."""
        bound = self.verify_content(command, entry)
        if bound.actor_identity_id != actor.identity_id or bound.actor_pk != actor.pk:
            raise _request_binding_mismatch()
        if bound.actor_access_epoch != actor.access_epoch:
            raise _authority_unavailable(bound.entry)(
                "actor access epoch changed after intake; nothing was done"
            )
        if bound.intake_id is not None:
            with connections[alias].cursor() as cursor:
                cursor.execute(
                    """
                    SELECT actor_id, actor_identity_id, actor_access_epoch, entry, action,
                           idempotency_key, request_reference, request_fingerprint,
                           received_at, target_organism_id
                      FROM public.core_commandintake
                     WHERE id = %s
                    """,
                    [bound.intake_id],
                )
                row = cursor.fetchone()
            row_binding = bound.parent if bound.parent is not None else bound
            expected = (
                row_binding.actor_pk,
                row_binding.actor_identity_id,
                row_binding.actor_access_epoch,
                row_binding.entry.value,
                row_binding.action,
                row_binding.idempotency_key,
                row_binding.request_reference,
                row_binding.request_fingerprint,
                row_binding.received_at,
                row_binding.target_organism_id,
            )
            if row != expected:
                raise _request_binding_mismatch()

    def child(self, child_entry: ChildEntry, *, command: object) -> BoundRequest:
        """Derive one governance stage as a child of this compound request.

        Requires this request to be content-verified, the pair (entry,
        child_entry) to be in the compound allow-list and a target organism.
        Checks the stage command's key and reference against
        _compound_stage_identifiers and its action against the stage, then
        fingerprints the child under its ChildEntry. The returned child is not
        itself content-verified: its stage executor verifies it.
        """
        if type(child_entry) is not ChildEntry or not self.content_verified:
            raise _request_binding_mismatch()
        if (self.entry, child_entry) not in _COMPOUND_CHILDREN:
            raise _request_binding_mismatch()
        if self.target_organism_id is None:
            raise _request_binding_mismatch()
        shape_entry = _CHILD_PUBLIC_ENTRIES[child_entry]
        shape = intake_shape(shape_entry, command)
        from src.intevia.services.organism_governance_service import (
            _compound_stage_identifiers,
        )

        identifiers = _compound_stage_identifiers(
            self.target_organism_id,
            self.idempotency_key,
        )
        expected_key, expected_reference = (
            identifiers[0:2]
            if child_entry is ChildEntry.REQUEST_STAGE
            else identifiers[2:4]
        )
        if (
            shape.idempotency_key != expected_key
            or shape.request_reference != expected_reference
        ):
            raise _request_binding_mismatch()
        action = _action_for(shape_entry, shape.action)
        if action != _CHILD_ACTIONS[child_entry]:
            raise _request_binding_mismatch()
        fingerprint = intake_fingerprint(
            entry=child_entry,
            action=action,
            actor_identity_id=self.actor_identity_id,
            actor_access_epoch=self.actor_access_epoch,
            request_reference=shape.request_reference,
            idempotency_key=shape.idempotency_key,
            target=shape.target,
            command=shape.command,
            omitted=shape.omitted,
        )
        return BoundRequest(
            entry=child_entry,
            action=action,
            idempotency_key=shape.idempotency_key,
            request_reference=shape.request_reference,
            request_fingerprint=fingerprint,
            actor_pk=self.actor_pk,
            actor_identity_id=self.actor_identity_id,
            actor_access_epoch=self.actor_access_epoch,
            received_at=self.received_at,
            intake_id=self.intake_id,
            target_organism_id=self.target_organism_id,
            first=False,
            parent=self,
            _sentinel=_CONSTRUCTOR_SENTINEL,
        )


@dataclass(frozen=True, slots=True)
class Slot:
    action: str
    idempotency_key: str
    clause: str


@dataclass(frozen=True, slots=True)
class IntakeBinding:
    actor_id: int
    entry: IntakeEntry
    action: str
    idempotency_key: str
    target_organism_id: UUID | None
    received_at: datetime


@dataclass(frozen=True, slots=True)
class IntakeRecord:
    id: int
    actor_id: int
    action: str
    idempotency_key: str
    kind: str
    evaluated_at: datetime | None


@dataclass(frozen=True, slots=True)
class UnaccountedRecord:
    actor_id: int
    action: str
    idempotency_key: str
    kind: str
    record_id: int


class IntakeAdmissionTechnicalFailure(RuntimeError):
    """The request could not be safely admitted in the supported state."""


class IntakeUnexpectedHistory(IntakeAdmissionTechnicalFailure):
    """A pre-intake record cannot be attributed to an existing binding."""


ENTRY_ACTIONS: dict[IntakeEntry, str | None] = {
    IntakeEntry.FOUND_LIVING_ORGANISM: OrganismAction.FOUND_LIVING_ORGANISM.value,
    IntakeEntry.ADVANCE_PHASE: OrganismAction.TRANSITION_LIVING_ORGANISM.value,
    IntakeEntry.BIRTHING: OrganismAction.TRANSITION_LIVING_ORGANISM.value,
    IntakeEntry.ADOPT_GOVERNING_INSTRUMENT: OrganismAction.ADOPT_GOVERNING_INSTRUMENT.value,
    IntakeEntry.CREATE_GOVERNANCE_CIRCLE: OrganismAction.CREATE_DORMANT_CIRCLE.value,
    IntakeEntry.REQUEST_GOVERNANCE_SEAT: OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
    IntakeEntry.DECIDE_GOVERNANCE_SEAT: OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
    IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND: OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
    IntakeEntry.SEAT_GOVERNANCE_CIRCLE: OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
    IntakeEntry.SUBMIT_APPLICATION: OrganismAction.SUBMIT_APPLICATION.value,
    IntakeEntry.WITHDRAW_APPLICATION: OrganismAction.WITHDRAW_APPLICATION.value,
    IntakeEntry.CLAIM_APPLICATION: OrganismAction.CLAIM_APPLICATION.value,
    IntakeEntry.REASSIGN_APPLICATION: OrganismAction.REASSIGN_APPLICATION.value,
    IntakeEntry.DECIDE_APPLICATION: OrganismAction.DECIDE_APPLICATION.value,
    IntakeEntry.SAVE_GATEWAY_DRAFT: None,
    IntakeEntry.EFFECT_GATEWAY_VERSION: None,
    IntakeEntry.OPEN_MEMBERSHIP: OrganismAction.TRANSITION_MEMBERSHIP.value,
    IntakeEntry.TRANSITION_MEMBERSHIP: OrganismAction.TRANSITION_MEMBERSHIP.value,
    IntakeEntry.PROPOSE_ASSIGNMENT: OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
    IntakeEntry.TRANSITION_ASSIGNMENT: OrganismAction.TRANSITION_CONTEXTUAL_ROLE.value,
    IntakeEntry.RECORD_OFFICE_CONSENT: OrganismAction.RECORD_OFFICE_CONSENT.value,
    IntakeEntry.ACTIVATE_BETA: OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
}
_GATEWAY_ACTIONS = frozenset(
    {
        OrganismAction.EFFECT_APPLICATION_SETTINGS.value,
        OrganismAction.EFFECT_APPLICATION_QUESTION.value,
        OrganismAction.EFFECT_GOVERNANCE_CONFIGURATION.value,
    }
)
_DIRECT_ONLY = frozenset(
    entry for entry in IntakeEntry if entry not in COMPOUND_ENTRIES and entry is not IntakeEntry.BIRTHING
)
SLOT_CLAUSES: dict[IntakeEntry, tuple[str, ...]] = {
    entry: ("D",) for entry in _DIRECT_ONLY
}
SLOT_CLAUSES[IntakeEntry.BIRTHING] = ("D", "C1")
for _entry in COMPOUND_ENTRIES:
    SLOT_CLAUSES[_entry] = ("C2",)

G1 = frozenset(
    {
        IntakeEntry.ADVANCE_PHASE,
        IntakeEntry.BIRTHING,
        IntakeEntry.ACTIVATE_BETA,
    }
)
G2 = frozenset(
    {
        IntakeEntry.REQUEST_GOVERNANCE_SEAT,
        IntakeEntry.DECIDE_GOVERNANCE_SEAT,
        IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
        IntakeEntry.SEAT_GOVERNANCE_CIRCLE,
    }
)
G3 = frozenset(
    {
        IntakeEntry.SAVE_GATEWAY_DRAFT,
        IntakeEntry.EFFECT_GATEWAY_VERSION,
    }
)
G4 = frozenset(
    {
        IntakeEntry.OPEN_MEMBERSHIP,
        IntakeEntry.TRANSITION_MEMBERSHIP,
    }
)
G5 = frozenset(
    {
        IntakeEntry.PROPOSE_ASSIGNMENT,
        IntakeEntry.TRANSITION_ASSIGNMENT,
    }
)
ACTIVATION_GROUPS: dict[str, frozenset[IntakeEntry]] = {
    "G1": G1,
    "G2": G2,
    "G3": G3,
    "G4": G4,
    "G5": G5,
}
ACTIVE_ENTRIES: frozenset[IntakeEntry] = frozenset(
    {
        IntakeEntry.SUBMIT_APPLICATION,
        IntakeEntry.WITHDRAW_APPLICATION,
        IntakeEntry.CLAIM_APPLICATION,
        IntakeEntry.REASSIGN_APPLICATION,
        IntakeEntry.DECIDE_APPLICATION,
        IntakeEntry.OPEN_MEMBERSHIP,
        IntakeEntry.TRANSITION_MEMBERSHIP,
        IntakeEntry.PROPOSE_ASSIGNMENT,
        IntakeEntry.TRANSITION_ASSIGNMENT,
        IntakeEntry.RECORD_OFFICE_CONSENT,
    }
)


def _request_binding_mismatch() -> IntakeAdmissionTechnicalFailure:
    return IntakeAdmissionTechnicalFailure(
        "the request being executed is not the request that was admitted; nothing was done"
    )


def _canonical_value(value: object) -> object:
    if isinstance(value, datetime):
        return canonical_timestamp(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def intake_fingerprint(
    *,
    entry: IntakeEntry | ChildEntry,
    action: str,
    actor_identity_id: UUID,
    actor_access_epoch: int,
    request_reference: str,
    idempotency_key: str,
    target: dict[str, object],
    command: dict[str, object],
    omitted: tuple[str, ...],
) -> str:
    """Fingerprint the submitted request without assigning or incorporating receipt time."""
    payload = {
        "schema": FINGERPRINT_SCHEMA,
        "entry": entry.value,
        "action": action,
        "actor_identity_id": str(actor_identity_id),
        "actor_access_epoch": actor_access_epoch,
        "request_reference": request_reference,
        "idempotency_key": idempotency_key,
        "target": _canonical_value(target),
        "command": _canonical_value(command),
        "omitted": list(omitted),
    }
    return hashlib.sha256(DOMAIN_INTAKE + canonical_json_bytes(payload)).hexdigest()


def _action_for(entry: IntakeEntry, action: str | None) -> str:
    expected = ENTRY_ACTIONS[entry]
    if expected is not None:
        if action is not None and action != expected:
            raise ValueError(f"action does not match {entry.value}")
        return expected
    if action not in _GATEWAY_ACTIONS:
        raise ValueError(f"{entry.value} requires a supported gateway action")
    return action


def slots(
    entry: IntakeEntry,
    key: str,
    target_organism_id: UUID | None,
    *,
    action: str | None = None,
) -> tuple[Slot, ...]:
    """Return the direct, Birthing-child, or compound-stage slots for an entry."""
    entry = IntakeEntry(entry)
    clauses = SLOT_CLAUSES[entry]
    resolved_action = _action_for(entry, action)
    result: list[Slot] = []
    if "D" in clauses:
        result.append(Slot(resolved_action, key, "D"))
    if "C1" in clauses:
        result.append(
            Slot(OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value, key, "C1")
        )
    if "C2" in clauses:
        if target_organism_id is None:
            raise ValueError("compound intake slots require target_organism_id")
        from src.intevia.services.organism_governance_service import (
            _compound_stage_identifiers,
        )

        request_key, _, admission_key, _ = _compound_stage_identifiers(
            target_organism_id,
            key,
        )
        result.extend(
            (
                Slot(OrganismAction.REQUEST_GOVERNANCE_SEAT.value, request_key, "C2"),
                Slot(
                    OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
                    admission_key,
                    "C2",
                ),
            )
        )
    return tuple(result)


def accounts_for(binding: IntakeBinding, record: IntakeRecord) -> bool:
    """Apply the shared actor, slot-kind, and decision-time recognition relation."""
    if record.actor_id != binding.actor_id:
        return False
    for slot in slots(
        binding.entry,
        binding.idempotency_key,
        binding.target_organism_id,
        action=binding.action,
    ):
        if (record.action, record.idempotency_key) != (
            slot.action,
            slot.idempotency_key,
        ):
            continue
        if record.kind == "receipt" and slot.clause == "C1":
            continue
        if record.kind == "decision" and record.evaluated_at != binding.received_at:
            continue
        return True
    return False


def _require_admission_state(alias: str) -> None:
    """Refuse unsupported writers or transaction state using attribute reads only."""
    connection = connections[alias]
    if connection.vendor != "postgresql":
        raise S015CommandMutexUnavailable("command intake requires PostgreSQL")
    if alias != "default":
        raise IntakeAdmissionTechnicalFailure(
            "command intake must use the default writer connection"
        )
    if connection.in_atomic_block:
        raise S015CommandAtomicityRequired(
            "command intake requires a top-level transaction; nothing was saved"
        )
    if connection.get_autocommit() is not True:
        raise S015CommandAtomicityRequired(
            "command intake requires autocommit; a manual transaction is open"
        )
    if connection.needs_rollback:
        raise S015CommandAtomicityRequired(
            "command intake cannot use a connection with a pending rollback"
        )


def _require_not_in_atomic_block(alias: str) -> None:
    """Refuse to construct a command boundary from inside an atomic block."""
    if connections[alias].in_atomic_block:
        raise S015CommandAtomicityRequired(
            "command intake requires a top-level transaction; nothing was saved"
        )


def _resolve_actor(alias: str, credential: object) -> ActorRef:
    """Resolve one Identity by its credential with a single unlocked SELECT."""
    credential_pk = getattr(credential, "pk", None)
    if type(credential_pk) is not int:
        raise ValueError("request credential does not identify an Identity")
    with connections[alias].cursor() as cursor:
        cursor.execute(
            "SELECT id, identity_id, access_epoch "
            "FROM public.core_identity WHERE credential_id = %s",
            [credential_pk],
        )
        row = cursor.fetchone()
    if row is None:
        raise ValueError("request credential does not identify an Identity")
    return ActorRef(pk=row[0], identity_id=row[1], access_epoch=row[2])


def _existing_row(alias: str, actor_pk: int, action: str, key: str):
    with connections[alias].cursor() as cursor:
        cursor.execute(
            """
            SELECT id, entry, request_reference, request_fingerprint,
                   fingerprint_schema, received_at, target_organism_id
              FROM public.core_commandintake
             WHERE actor_id = %s AND action = %s AND idempotency_key = %s
            """,
            [actor_pk, action, key],
        )
        return cursor.fetchone()


def _history_records(
    alias: str,
    actor_pk: int,
    candidate_slots: tuple[Slot, ...],
) -> list[IntakeRecord]:
    if not candidate_slots:
        return []
    pair_sql = " OR ".join(
        "(action = %s AND idempotency_key = %s)" for _ in candidate_slots
    )
    pair_params = [
        value
        for slot in candidate_slots
        for value in (slot.action, slot.idempotency_key)
    ]
    with connections[alias].cursor() as cursor:
        cursor.execute(
            f"""
            SELECT 'receipt', id, actor_id, action, idempotency_key, NULL::timestamptz
              FROM public.core_organismcommandreceipt
             WHERE actor_id = %s AND ({pair_sql})
            UNION ALL
            SELECT 'decision', id, actor_id, action, idempotency_key, evaluated_at
              FROM public.core_authoritydecision
             WHERE actor_id = %s AND ({pair_sql})
            """,
            [actor_pk, *pair_params, actor_pk, *pair_params],
        )
        rows = cursor.fetchall()
    return [
        IntakeRecord(
            id=row[1],
            actor_id=row[2],
            action=row[3],
            idempotency_key=row[4],
            kind=row[0],
            evaluated_at=row[5],
        )
        for row in rows
    ]


def _candidate_bindings(
    alias: str,
    actor_pk: int,
    candidate_slots: tuple[Slot, ...],
) -> list[IntakeBinding]:
    pair_sql = " OR ".join(
        "(action = %s AND idempotency_key = %s)" for _ in candidate_slots
    )
    pair_params = [
        value
        for slot in candidate_slots
        for value in (slot.action, slot.idempotency_key)
    ]
    where = f"entry IN (%s, %s)"
    params: list[object] = [actor_pk, *_COMPOUND_ENTRY_VALUES]
    if candidate_slots:
        where += f" OR ({pair_sql})"
        params.extend(pair_params)
    with connections[alias].cursor() as cursor:
        cursor.execute(
            f"""
            SELECT actor_id, entry, action, idempotency_key,
                   target_organism_id, received_at
              FROM public.core_commandintake
             WHERE actor_id = %s AND ({where})
            """,
            params,
        )
        rows = cursor.fetchall()
    return [
        IntakeBinding(
            actor_id=row[0],
            entry=IntakeEntry(row[1]),
            action=row[2],
            idempotency_key=row[3],
            target_organism_id=row[4],
            received_at=row[5],
        )
        for row in rows
    ]


def _refuse_unexpected_history(
    alias: str,
    actor: ActorRef,
    entry: IntakeEntry,
    action: str,
    shape: IntakeShape,
):
    candidate_slots = slots(
        entry,
        shape.idempotency_key,
        shape.target_organism_id,
        action=action,
    )
    records = _history_records(alias, actor.pk, candidate_slots)
    if not records:
        return None
    bindings = _candidate_bindings(alias, actor.pk, candidate_slots)
    unaccounted = [
        record
        for record in records
        if not any(accounts_for(binding, record) for binding in bindings)
    ]
    if not unaccounted:
        return None
    existing = _existing_row(alias, actor.pk, action, shape.idempotency_key)
    if existing is not None:
        return existing
    record = unaccounted[0]
    raise IntakeUnexpectedHistory(
        "a record for this request key exists from before the intake was enabled; "
        f"unaccounted {record.kind} id={record.id} action={record.action} "
        f"key={record.idempotency_key}; nothing was admitted"
    )


def _normalise_shape(entry: IntakeEntry, shape: IntakeShape) -> str:
    action = _action_for(entry, shape.action)
    if type(shape.idempotency_key) is not str or not shape.idempotency_key:
        raise ValueError("idempotency_key must be a non-empty canonical string")
    if type(shape.request_reference) is not str or not shape.request_reference:
        raise ValueError("request_reference must be a non-empty canonical string")
    if type(shape.target) is not dict or type(shape.command) is not dict:
        raise TypeError("intake target and command must be objects")
    if entry in COMPOUND_ENTRIES:
        if not isinstance(shape.target_organism_id, UUID):
            raise ValueError("compound intake requires target_organism_id")
    elif shape.target_organism_id is not None:
        raise ValueError("target_organism_id is only stored for compound entries")
    if type(shape.omitted) is not tuple or any(type(name) is not str for name in shape.omitted):
        raise TypeError("omitted must be a tuple of field names")
    return action


def _application_keys_and_times(
    request_reference: object,
    idempotency_key: object,
    occurred_at: object,
    effective_at: object,
    received_at: object,
) -> tuple[str, str, datetime, datetime]:
    request_reference = _canonical_key(request_reference, "request_reference", 128)
    idempotency_key = _canonical_key(idempotency_key, "idempotency_key", 120)
    occurred_at = _validate_timestamp(occurred_at, "occurred_at")
    effective_at = _validate_timestamp(effective_at, "effective_at")
    if received_at is not None:
        raise ValueError("received_at is assigned by INTEVIA")
    return request_reference, idempotency_key, occurred_at, effective_at


def _submit_application_shape(command: object) -> IntakeShape:
    from src.intevia.services.organism_application_service import SubmitApplicationCommand

    if type(command) is not SubmitApplicationCommand:
        raise TypeError("application entry requires SubmitApplicationCommand")
    request_reference, idempotency_key, occurred_at, effective_at = _application_keys_and_times(
        command.request_reference,
        command.idempotency_key,
        command.occurred_at,
        command.effective_at,
        command.received_at,
    )
    organism_uuid = _canonical_uuid(command.living_organism_id, "living_organism_id")
    predecessor_uuid = (
        None
        if command.predecessor_reference is None
        else _canonical_uuid(command.predecessor_reference, "predecessor_reference")
    )
    return IntakeShape(
        idempotency_key=idempotency_key,
        request_reference=request_reference,
        target={"living_organism_id": organism_uuid},
        command={
            "occurred_at": occurred_at,
            "effective_at": effective_at,
            "predecessor_reference": predecessor_uuid,
            "answer": command.answer,
        },
        omitted=(),
        target_organism_id=None,
    )


def _withdraw_application_shape(command: object) -> IntakeShape:
    from src.intevia.services.organism_application_service import WithdrawApplicationCommand

    if type(command) is not WithdrawApplicationCommand:
        raise TypeError("application entry requires WithdrawApplicationCommand")
    request_reference, idempotency_key, occurred_at, effective_at = _application_keys_and_times(
        command.request_reference,
        command.idempotency_key,
        command.occurred_at,
        command.effective_at,
        command.received_at,
    )
    application_uuid = _canonical_uuid(command.application_uuid, "application_uuid")
    return IntakeShape(
        idempotency_key=idempotency_key,
        request_reference=request_reference,
        target={"application_uuid": application_uuid},
        command={"occurred_at": occurred_at, "effective_at": effective_at},
        omitted=(),
        target_organism_id=None,
    )


def _claim_application_shape(command: object) -> IntakeShape:
    from src.intevia.services.organism_application_service import ClaimApplicationCommand

    if type(command) is not ClaimApplicationCommand:
        raise TypeError("application entry requires ClaimApplicationCommand")
    request_reference, idempotency_key, occurred_at, effective_at = _application_keys_and_times(
        command.request_reference,
        command.idempotency_key,
        command.occurred_at,
        command.effective_at,
        command.received_at,
    )
    application_uuid = _canonical_uuid(command.application_uuid, "application_uuid")
    return IntakeShape(
        idempotency_key=idempotency_key,
        request_reference=request_reference,
        target={"application_uuid": application_uuid},
        command={"occurred_at": occurred_at, "effective_at": effective_at},
        omitted=(),
        target_organism_id=None,
    )


def _reassign_application_shape(command: object) -> IntakeShape:
    from src.intevia.services.organism_application_service import ReassignApplicationCommand

    if type(command) is not ReassignApplicationCommand:
        raise TypeError("application entry requires ReassignApplicationCommand")
    request_reference, idempotency_key, occurred_at, effective_at = _application_keys_and_times(
        command.request_reference,
        command.idempotency_key,
        command.occurred_at,
        command.effective_at,
        command.received_at,
    )
    application_uuid = _canonical_uuid(command.application_uuid, "application_uuid")
    return IntakeShape(
        idempotency_key=idempotency_key,
        request_reference=request_reference,
        target={"application_uuid": application_uuid},
        command={"occurred_at": occurred_at, "effective_at": effective_at},
        omitted=(),
        target_organism_id=None,
    )


def _decide_application_shape(command: object) -> IntakeShape:
    from src.intevia.services.organism_application_service import DecideApplicationCommand

    if type(command) is not DecideApplicationCommand:
        raise TypeError("application entry requires DecideApplicationCommand")
    outcome = _canonical_key(command.outcome, "outcome", 7)
    if outcome not in {"ACCEPT", "DECLINE"}:
        raise ValueError("outcome must be ACCEPT or DECLINE")
    request_reference, idempotency_key, occurred_at, effective_at = _application_keys_and_times(
        command.request_reference,
        command.idempotency_key,
        command.occurred_at,
        command.effective_at,
        command.received_at,
    )
    application_uuid = _canonical_uuid(command.application_uuid, "application_uuid")
    return IntakeShape(
        idempotency_key=idempotency_key,
        request_reference=request_reference,
        target={"application_uuid": application_uuid},
        command={"occurred_at": occurred_at, "effective_at": effective_at, "outcome": outcome},
        omitted=(),
        target_organism_id=None,
    )


APPLICATION_SHAPES: dict[IntakeEntry, Callable[[object], IntakeShape]] = {
    IntakeEntry.SUBMIT_APPLICATION: _submit_application_shape,
    IntakeEntry.WITHDRAW_APPLICATION: _withdraw_application_shape,
    IntakeEntry.CLAIM_APPLICATION: _claim_application_shape,
    IntakeEntry.REASSIGN_APPLICATION: _reassign_application_shape,
    IntakeEntry.DECIDE_APPLICATION: _decide_application_shape,
}


def application_shape(entry: IntakeEntry, command: object) -> IntakeShape:
    """Return the pure submitted-command projection for an active application entry."""
    if type(entry) is not IntakeEntry or entry not in APPLICATION_SHAPES:
        raise ValueError("entry has no application intake shape")
    return APPLICATION_SHAPES[entry](command)


def _membership_shape(entry: IntakeEntry, command: object) -> IntakeShape:
    from src.intevia.services.organism_membership_service import (
        OpenMembershipCommand,
        TransitionMembershipCommand,
        _canonical_reason,
        _validate_member_state,
    )

    if entry is IntakeEntry.OPEN_MEMBERSHIP and type(command) is OpenMembershipCommand:
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        reason_class = _canonical_reason(command.reason_class)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        if command.received_at is not None:
            raise ValueError("received_at is assigned by INTEVIA")
        target = {
            "living_organism_id": _canonical_uuid(
                command.living_organism_id, "living_organism_id"
            ),
            "member_identity_id": _canonical_uuid(
                command.member_identity_id, "member_identity_id"
            ),
        }
        payload = {
            "occurred_at": occurred_at,
            "effective_at": effective_at,
            "reason_class": reason_class,
        }
    elif entry is IntakeEntry.TRANSITION_MEMBERSHIP and type(command) is TransitionMembershipCommand:
        request_reference = _canonical_key(command.request_reference, "request_reference", 128)
        idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
        reason_class = _canonical_reason(command.reason_class)
        occurred_at = _validate_timestamp(command.occurred_at, "occurred_at")
        effective_at = _validate_timestamp(command.effective_at, "effective_at")
        if command.received_at is not None:
            raise ValueError("received_at is assigned by INTEVIA")
        target = {
            "membership_uuid": _canonical_uuid(command.membership_uuid, "membership_uuid")
        }
        payload = {
            "occurred_at": occurred_at,
            "effective_at": effective_at,
            "resulting_state": _validate_member_state(command.resulting_state),
            "reason_class": reason_class,
        }
    else:
        raise TypeError(f"{entry.value} requires its exact membership command type")
    return IntakeShape(
        idempotency_key=idempotency_key,
        request_reference=request_reference,
        target=target,
        command=payload,
        omitted=(),
        target_organism_id=None,
    )


def _assignment_shape(entry: IntakeEntry, command: object) -> IntakeShape:
    from src.intevia.services.organism_role_service import (
        ProposeAssignmentCommand,
        S015AssignmentTermRefused,
        TransitionAssignmentCommand,
        _validate_event_action,
        _validate_resulting_state,
        _validate_term,
    )

    if entry is IntakeEntry.PROPOSE_ASSIGNMENT and type(command) is ProposeAssignmentCommand:
        request_reference, idempotency_key, occurred_at, effective_at = _application_keys_and_times(
            command.request_reference,
            command.idempotency_key,
            command.occurred_at,
            command.effective_at,
            command.received_at,
        )
        target = {
            "membership_uuid": _canonical_uuid(command.membership_uuid, "membership_uuid"),
            "role_definition_uuid": _canonical_uuid(
                command.role_definition_uuid, "role_definition_uuid"
            ),
        }
        term_state, term = _validate_term(
            effective_until_state=command.effective_until_state,
            effective_until=command.effective_until,
            effective_at=effective_at,
        )
        payload = {
            "occurred_at": occurred_at,
            "effective_at": effective_at,
            "effective_until_state": term_state,
            "effective_until": term,
        }
    elif entry is IntakeEntry.TRANSITION_ASSIGNMENT and type(command) is TransitionAssignmentCommand:
        request_reference, idempotency_key, occurred_at, effective_at = _application_keys_and_times(
            command.request_reference,
            command.idempotency_key,
            command.occurred_at,
            command.effective_at,
            command.received_at,
        )
        assignment_uuid = _canonical_uuid(command.assignment_uuid, "assignment_uuid")
        action = _validate_event_action(command.action)
        resulting_state = _validate_resulting_state(command.resulting_state)
        if action == "ASSIGN":
            if command.effective_until_state is None:
                raise S015AssignmentTermRefused(
                    "ASSIGN requires effective_until_state (the appointing decision supplies the term)"
                )
            term_state, term = _validate_term(
                effective_until_state=command.effective_until_state,
                effective_until=command.effective_until,
                effective_at=effective_at,
            )
        else:
            if command.effective_until_state is not None or command.effective_until is not None:
                raise S015AssignmentTermRefused(
                    f"only ASSIGN establishes a term; effective_until fields are refused on {action}"
                )
            term_state, term = None, None
        target = {
            "assignment_uuid": assignment_uuid
        }
        payload = {
            "occurred_at": occurred_at,
            "effective_at": effective_at,
            "action": action,
            "resulting_state": resulting_state,
            "effective_until_state": term_state,
            "effective_until": term,
        }
    else:
        raise TypeError(f"{entry.value} requires its exact assignment command type")
    return IntakeShape(
        idempotency_key=idempotency_key,
        request_reference=request_reference,
        target=target,
        command=payload,
        omitted=(),
        target_organism_id=None,
    )


def _office_consent_shape(command: object) -> IntakeShape:
    from src.intevia.services.organism_office_consent_service import (
        RecordOfficeConsentCommand,
        _ACTIONS,
    )

    if type(command) is not RecordOfficeConsentCommand:
        raise TypeError("command must be a RecordOfficeConsentCommand")
    request_reference, idempotency_key, occurred_at, effective_at = _application_keys_and_times(
        command.request_reference,
        command.idempotency_key,
        command.occurred_at,
        command.effective_at,
        command.received_at,
    )
    if type(command.action) is not str or command.action not in _ACTIONS:
        raise ValueError("action must be CONSENT, DECLINE, or WITHDRAW")
    return IntakeShape(
        idempotency_key=idempotency_key,
        request_reference=request_reference,
        target={
            "assignment_uuid": _canonical_uuid(command.assignment_uuid, "assignment_uuid"),
            "proposal_event_uuid": _canonical_uuid(
                command.proposal_event_uuid, "proposal_event_uuid"
            ),
        },
        command={
            "occurred_at": occurred_at,
            "effective_at": effective_at,
            "action": command.action,
        },
        omitted=(),
        target_organism_id=None,
    )


def _governance_request_shape(command: object) -> IntakeShape:
    """Project a governance request from its submitted identifiers and values."""
    from src.intevia.services.organism_governance_service import (
        RequestGovernanceSeatCommand,
        _SEAT_ID,
    )

    if type(command) is not RequestGovernanceSeatCommand:
        raise TypeError("governance request entry requires RequestGovernanceSeatCommand")
    request_reference = _canonical_key(command.request_reference, "request_reference", 128)
    idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
    organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
    circle_uuid = _canonical_uuid(command.circle_uuid, "circle_uuid")
    rule_uuid = _canonical_uuid(command.seat_rule_uuid, "seat_rule_uuid")
    if type(command.seat_id) is not str or _SEAT_ID.fullmatch(command.seat_id) is None:
        raise ValueError("seat_id is invalid")
    effective_at = (
        None
        if command.effective_at is None
        else _validate_timestamp(command.effective_at, "effective_at")
    )
    return IntakeShape(
        idempotency_key=idempotency_key,
        request_reference=request_reference,
        target={
            "living_organism_uuid": organism_uuid,
            "circle_uuid": circle_uuid,
            "seat_rule_uuid": rule_uuid,
            "seat_id": command.seat_id,
        },
        command={"effective_at": effective_at},
        omitted=(),
        target_organism_id=None,
    )


def _governance_decision_shape(
    entry: IntakeEntry,
    command: object,
) -> IntakeShape:
    """Project either the standalone decision or its compound parent form."""
    from src.intevia.services.organism_governance_service import (
        DecideGovernanceSeatCommand,
        _SEAT_ID,
    )

    if type(command) is not DecideGovernanceSeatCommand:
        raise TypeError("governance decision entry requires DecideGovernanceSeatCommand")
    compound = entry is IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND
    if compound != (command.request_uuid is None):
        raise ValueError("request_uuid does not match the governance decision entry")
    request_reference = _canonical_key(command.request_reference, "request_reference", 128)
    idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
    organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
    if type(command.outcome) is not str or command.outcome not in {"ACCEPT", "DECLINE"}:
        raise ValueError("outcome must be ACCEPT or DECLINE")
    effective_at = (
        None
        if command.effective_at is None
        else _validate_timestamp(command.effective_at, "effective_at")
    )
    if compound:
        if (
            command.circle_uuid is None
            or command.seat_rule_uuid is None
            or type(command.seat_id) is not str
            or _SEAT_ID.fullmatch(command.seat_id) is None
        ):
            raise ValueError("self-appointment requires a circle and seat")
        circle_uuid = _canonical_uuid(command.circle_uuid, "circle_uuid")
        rule_uuid = _canonical_uuid(command.seat_rule_uuid, "seat_rule_uuid")
        target = {
            "living_organism_uuid": organism_uuid,
            "circle_uuid": circle_uuid,
            "seat_rule_uuid": rule_uuid,
            "seat_id": command.seat_id,
        }
        target_organism_id = organism_uuid
    else:
        if (
            command.circle_uuid is not None
            or command.seat_rule_uuid is not None
            or command.seat_id is not None
        ):
            raise ValueError("an existing request supplies its own circle and seat")
        request_uuid = _canonical_uuid(command.request_uuid, "request_uuid")
        target = {
            "living_organism_uuid": organism_uuid,
            "request_uuid": request_uuid,
        }
        target_organism_id = None
        if command.route not in {None, "CIRCLE_FOUNDING", "GOVERNANCE_CONSTITUTIONAL"}:
            raise ValueError("route is invalid")
    return IntakeShape(
        idempotency_key=idempotency_key,
        request_reference=request_reference,
        target=target,
        command={
            "outcome": command.outcome,
            "route": command.route,
            "effective_at": effective_at,
        },
        omitted=(),
        target_organism_id=target_organism_id,
    )


def _founding_seating_shape(command: object) -> IntakeShape:
    """Project a compound founding-seating command without performing reads."""
    from src.intevia.services.organism_governance_service import (
        SeatFoundingGovernanceCircleCommand,
        _SEAT_ID,
    )

    if type(command) is not SeatFoundingGovernanceCircleCommand:
        raise TypeError(
            "founding seating entry requires SeatFoundingGovernanceCircleCommand"
        )
    request_reference = _canonical_key(command.request_reference, "request_reference", 128)
    idempotency_key = _canonical_key(command.idempotency_key, "idempotency_key", 120)
    organism_uuid = _canonical_uuid(command.living_organism_uuid, "living_organism_uuid")
    circle_uuid = _canonical_uuid(command.circle_uuid, "circle_uuid")
    rule_uuid = _canonical_uuid(command.seat_rule_uuid, "seat_rule_uuid")
    if type(command.seat_id) is not str or _SEAT_ID.fullmatch(command.seat_id) is None:
        raise ValueError("seat_id is invalid")
    effective_at = (
        None
        if command.effective_at is None
        else _validate_timestamp(command.effective_at, "effective_at")
    )
    return IntakeShape(
        idempotency_key=idempotency_key,
        request_reference=request_reference,
        target={
            "living_organism_uuid": organism_uuid,
            "circle_uuid": circle_uuid,
            "seat_rule_uuid": rule_uuid,
            "seat_id": command.seat_id,
        },
        command={"effective_at": effective_at},
        omitted=(),
        target_organism_id=organism_uuid,
    )


def intake_shape(entry: IntakeEntry, command: object) -> IntakeShape:
    """Return the pure command projection used by verification and test binding.

    These governance shapes support direct stage executors only; the public
    governance route remains unchanged until PKT-CLK-3 redirects it.
    """
    if type(entry) is not IntakeEntry:
        raise ValueError("entry must be an IntakeEntry")
    if entry is IntakeEntry.REQUEST_GOVERNANCE_SEAT:
        return _governance_request_shape(command)
    if entry is IntakeEntry.DECIDE_GOVERNANCE_SEAT:
        return _governance_decision_shape(entry, command)
    if entry is IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND:
        return _governance_decision_shape(entry, command)
    if entry is IntakeEntry.SEAT_GOVERNANCE_CIRCLE:
        return _founding_seating_shape(command)
    if entry in APPLICATION_SHAPES:
        return application_shape(entry, command)
    if entry in {IntakeEntry.OPEN_MEMBERSHIP, IntakeEntry.TRANSITION_MEMBERSHIP}:
        return _membership_shape(entry, command)
    if entry in {IntakeEntry.PROPOSE_ASSIGNMENT, IntakeEntry.TRANSITION_ASSIGNMENT}:
        return _assignment_shape(entry, command)
    if entry is IntakeEntry.RECORD_OFFICE_CONSENT:
        return _office_consent_shape(command)
    raise ValueError("entry has no active intake shape")


def _authority_unavailable(entry: IntakeEntry | ChildEntry):
    """Return the authority-epoch refusal class for the served entry family."""
    if entry in {
        IntakeEntry.REQUEST_GOVERNANCE_SEAT,
        IntakeEntry.DECIDE_GOVERNANCE_SEAT,
        IntakeEntry.DECIDE_GOVERNANCE_SEAT_COMPOUND,
        IntakeEntry.SEAT_GOVERNANCE_CIRCLE,
        ChildEntry.REQUEST_STAGE,
        ChildEntry.ADMIT_STAGE,
    }:
        from src.intevia.services.organism_governance_service import (
            SBGGovernanceUnavailable,
        )

        return SBGGovernanceUnavailable
    if entry in {IntakeEntry.OPEN_MEMBERSHIP, IntakeEntry.TRANSITION_MEMBERSHIP}:
        from src.intevia.services.organism_membership_service import (
            S015MembershipAuthorityUnavailable,
        )

        return S015MembershipAuthorityUnavailable
    if entry in {IntakeEntry.PROPOSE_ASSIGNMENT, IntakeEntry.TRANSITION_ASSIGNMENT}:
        from src.intevia.services.organism_role_service import S015AssignmentAuthorityUnavailable

        return S015AssignmentAuthorityUnavailable
    if entry is IntakeEntry.RECORD_OFFICE_CONSENT:
        from src.intevia.services.organism_office_consent_service import (
            S015OfficeConsentAuthorityUnavailable,
        )

        return S015OfficeConsentAuthorityUnavailable
    from src.intevia.services.organism_application_service import (
        S015ApplicationAuthorityUnavailable,
    )

    return S015ApplicationAuthorityUnavailable


def _bound_from_row(
    row,
    *,
    entry: IntakeEntry,
    action: str,
    actor: ActorRef,
    shape: IntakeShape,
    fingerprint: str,
    first: bool,
) -> BoundRequest:
    _, stored_entry, reference, stored_fingerprint, stored_schema, received_at, target = row
    if stored_schema != FINGERPRINT_SCHEMA:
        raise S015CommandReplayMismatch(
            "request was received under an earlier request format"
        )
    if (
        stored_entry != entry.value
        or reference != shape.request_reference
        or stored_fingerprint != fingerprint
    ):
        raise S015CommandReplayMismatch(
            "a different request already used this key; nothing was done"
        )
    if target != shape.target_organism_id:
        raise S015CommandReplayMismatch(
            "a different request already used this key; nothing was done"
        )
    return BoundRequest(
        entry=entry,
        action=action,
        idempotency_key=shape.idempotency_key,
        request_reference=shape.request_reference,
        request_fingerprint=fingerprint,
        actor_pk=actor.pk,
        actor_identity_id=actor.identity_id,
        actor_access_epoch=actor.access_epoch,
        received_at=received_at,
        intake_id=row[0],
        target_organism_id=target,
        first=first,
        parent=None,
        _sentinel=_CONSTRUCTOR_SENTINEL,
    )


def admit_request(
    alias: str,
    entry: IntakeEntry,
    actor_ref: ActorRef,
    shape: IntakeShape,
) -> BoundRequest:
    """Commit or recover the immutable request binding before command execution.

    Database errors with SQLSTATE 55P03 become contention, those with another
    SQLSTATE become technical admission failures stating nothing was saved, and
    those without SQLSTATE become uncertain connection failures stating the
    request may or may not have been received.
    """
    _require_admission_state(alias)
    entry = IntakeEntry(entry)
    action = _normalise_shape(entry, shape)
    action_text = action if action is not None else entry.value
    fingerprint = intake_fingerprint(
        entry=entry,
        action=action,
        actor_identity_id=actor_ref.identity_id,
        actor_access_epoch=actor_ref.access_epoch,
        request_reference=shape.request_reference,
        idempotency_key=shape.idempotency_key,
        target=shape.target,
        command=shape.command,
        omitted=shape.omitted,
    )

    first = False
    try:
        row = _existing_row(alias, actor_ref.pk, action, shape.idempotency_key)
        if row is None:
            row = _refuse_unexpected_history(
                alias,
                actor_ref,
                entry,
                action,
                shape,
            )
        if row is None:
            with connections[alias].cursor() as cursor:
                cursor.execute(
                    INSERT_COMMAND_INTAKE_SQL,
                    [
                        actor_ref.pk,
                        actor_ref.identity_id,
                        actor_ref.access_epoch,
                        entry.value,
                        action,
                        shape.idempotency_key,
                        shape.request_reference,
                        fingerprint,
                        FINGERPRINT_SCHEMA,
                        shape.target_organism_id,
                    ],
                )
                inserted = cursor.fetchone()
            if inserted is not None:
                row = (
                    inserted[0],
                    entry.value,
                    shape.request_reference,
                    fingerprint,
                    FINGERPRINT_SCHEMA,
                    inserted[1],
                    shape.target_organism_id,
                )
                first = True
            else:
                row = _existing_row(alias, actor_ref.pk, action, shape.idempotency_key)
                if row is None:
                    raise IntakeAdmissionTechnicalFailure(
                        "the conflicting request binding could not be read back"
                    )
    except DatabaseError as exc:
        sqlstate = getattr(exc.__cause__, "sqlstate", None)
        if sqlstate == "55P03":
            raise S015CommandContended("55P03") from exc
        if sqlstate is not None:
            raise IntakeAdmissionTechnicalFailure(
                f"{action_text} could not be admitted because of a technical problem; "
                "nothing was saved. Try again; if it persists, report it."
            ) from exc
        raise IntakeAdmissionTechnicalFailure(
            f"{action_text} could not be confirmed because the connection to the database was lost; "
            "this request may or may not have been received. Send the same request again with the same "
            "request key: INTEVIA will find the earlier receipt or receive it now. If it persists, report it."
        ) from exc
    return _bound_from_row(
        row,
        entry=entry,
        action=action,
        actor=actor_ref,
        shape=shape,
        fingerprint=fingerprint,
        first=first,
    )


def _activation_entries(entries: Iterable[IntakeEntry]) -> frozenset[IntakeEntry]:
    requested = frozenset(IntakeEntry(entry) for entry in entries)
    if not requested:
        raise ValueError("activation requires at least one entry")
    expanded = set(requested)
    for group in ACTIVATION_GROUPS.values():
        if requested.intersection(group):
            expanded.update(group)
    return frozenset(expanded)


def _namespace_actions(entries: frozenset[IntakeEntry]) -> list[str]:
    actions: set[str] = set()
    for entry in entries:
        action = ENTRY_ACTIONS[entry]
        if action is None:
            actions.update(_GATEWAY_ACTIONS)
        else:
            actions.add(action)
        if entry is IntakeEntry.BIRTHING:
            actions.add(OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value)
        if entry in COMPOUND_ENTRIES:
            actions.update(
                {
                    OrganismAction.REQUEST_GOVERNANCE_SEAT.value,
                    OrganismAction.ADMIT_CIRCLE_PARTICIPANT.value,
                }
            )
    return sorted(actions)


def _materialise_slots(alias: str) -> None:
    with connections[alias].cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS pg_temp.h100_intake_slot")
        cursor.execute(
            """
            CREATE TEMPORARY TABLE h100_intake_slot (
                intake_id bigint NOT NULL,
                actor_id bigint NOT NULL,
                action varchar(64) NOT NULL,
                idempotency_key varchar(120) NOT NULL,
                received_at timestamp with time zone NOT NULL,
                clause char(2) NOT NULL
            ) ON COMMIT DROP
            """
        )
        cursor.execute(
            """
            SELECT id, actor_id, entry, action, idempotency_key,
                   target_organism_id, received_at
              FROM public.core_commandintake
            """
        )
        rows = cursor.fetchall()
        values = []
        for intake_id, actor_id, entry_value, action, key, target, received_at in rows:
            binding_entry = IntakeEntry(entry_value)
            values.extend(
                (
                    intake_id,
                    actor_id,
                    slot.action,
                    slot.idempotency_key,
                    received_at,
                    slot.clause,
                )
                for slot in slots(binding_entry, key, target, action=action)
            )
        if values:
            cursor.executemany(
                """
                INSERT INTO pg_temp.h100_intake_slot
                    (intake_id, actor_id, action, idempotency_key, received_at, clause)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    value for value in values
                ],
            )


def run_activation_step(
    alias: str,
    entries: Iterable[IntakeEntry],
) -> list[UnaccountedRecord]:
    """Check every pre-existing receipt and decision in the entries' activation namespace."""
    expanded = _activation_entries(entries)
    namespace = _namespace_actions(expanded)
    if not namespace:
        return []
    with transaction.atomic(using=alias):
        _materialise_slots(alias)
        with connections[alias].cursor() as cursor:
            cursor.execute(
                """
                SELECT 'receipt', r.id, r.actor_id, r.action, r.idempotency_key
                  FROM public.core_organismcommandreceipt r
                 WHERE r.action = ANY(%s)
                   AND NOT EXISTS (
                       SELECT 1
                         FROM pg_temp.h100_intake_slot s
                        WHERE s.actor_id = r.actor_id
                          AND s.action = r.action
                          AND s.idempotency_key = r.idempotency_key
                          AND s.clause <> 'C1'
                   )
                UNION ALL
                SELECT 'decision', d.id, d.actor_id, d.action, d.idempotency_key
                  FROM public.core_authoritydecision d
                 WHERE d.action = ANY(%s)
                   AND NOT EXISTS (
                       SELECT 1
                         FROM pg_temp.h100_intake_slot s
                        WHERE s.actor_id = d.actor_id
                          AND s.action = d.action
                          AND s.idempotency_key = d.idempotency_key
                          AND s.received_at = d.evaluated_at
                   )
                ORDER BY 1, 2
                """,
                [namespace, namespace],
            )
            rows = cursor.fetchall()
    return [
        UnaccountedRecord(
            actor_id=row[2],
            action=row[3],
            idempotency_key=row[4],
            kind=row[0],
            record_id=row[1],
        )
        for row in rows
    ]


__all__ = [
    "ACTIVE_ENTRIES",
    "ACTIVATION_GROUPS",
    "ActorRef",
    "BoundRequest",
    "ChildEntry",
    "DOMAIN_INTAKE",
    "ENTRY_ACTIONS",
    "FINGERPRINT_SCHEMA",
    "G1",
    "G2",
    "G3",
    "G4",
    "G5",
    "IntakeAdmissionTechnicalFailure",
    "IntakeBinding",
    "IntakeEntry",
    "IntakeRecord",
    "IntakeShape",
    "IntakeUnexpectedHistory",
    "INSERT_COMMAND_INTAKE_SQL",
    "SLOT_CLAUSES",
    "Slot",
    "UnaccountedRecord",
    "accounts_for",
    "admit_request",
    "intake_fingerprint",
    "intake_shape",
    "run_activation_step",
    "slots",
]
