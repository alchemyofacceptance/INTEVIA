"""SERVICE-owned command service for S012 Activity orchestration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from django.contrib.auth.models import User
from django.db import IntegrityError, connections, transaction

from core.models import (
    Identity,
    Service,
    ServiceActivity,
    ServiceActivityAssignment,
    ServiceActivityEvidenceReference,
    ServiceActivityReview,
    ServiceActivityTransition,
    ServiceVersion,
    ServiceWorkSubmission,
)
from src.intevia.services.service_authority import (
    ServiceCommandAuthority,
    ServiceCommandAuthorityRequest,
    ServiceCommandNotAuthorised,
    canonical_timestamp,
)

InitiatingDomain = ServiceActivity.InitiatingDomain
ServiceActivityState = ServiceActivity.State
ServiceCommandAction = ServiceActivityTransition.Action
ServiceActivityEvidenceKind = ServiceActivityEvidenceReference.Kind

EvidenceResult = tuple[tuple[ServiceActivityEvidenceKind, str], ...]

_COMMAND_DOMAIN = b"INTEVIA:S012:COMMAND:v1\x00"
_COMMAND_SCHEMA = "intevia.s012.command.v1"
_TARGET_DOMAIN = b"INTEVIA:S012:AUTHORITY_TARGET:v1\x00"
_TARGET_SCHEMA = "intevia.s012.authority-target.v1"
_LINEAGE_DOMAIN = b"INTEVIA:S012:TRANSITION_LINEAGE:v1\x00"
_LINEAGE_SCHEMA = "intevia.s012.lineage.v1"
_IDEM_CONSTRAINT = "s012_activity_actor_action_idem_uniq"

TERMINAL_STATES = frozenset({
    ServiceActivityState.COMPLETED,
    ServiceActivityState.DECLINED,
    ServiceActivityState.CANCELLED,
})

CANCEL_SOURCES = frozenset({
    ServiceActivityState.UNASSIGNED,
    ServiceActivityState.ASSIGNED,
    ServiceActivityState.IN_PROGRESS,
    ServiceActivityState.SUBMITTED,
    ServiceActivityState.REVIEWED,
})


class ServiceActivityCommandError(Exception):
    pass


class ServiceActivityConflict(ServiceActivityCommandError):
    pass


class ServiceActivityCrossEpochConflict(ServiceActivityConflict):
    pass


class ServiceActivityPayloadConflict(ServiceActivityConflict):
    pass


class ServiceActivityMalformedReplay(ServiceActivityConflict):
    pass


class ServiceActivityLifecycleError(ServiceActivityCommandError):
    pass


class ServiceActivityValidationError(ServiceActivityCommandError):
    pass


class ServiceActivityNotFound(ServiceActivityCommandError):
    pass


class ServiceActivityActorError(ServiceActivityCommandError):
    pass


@dataclass(frozen=True, slots=True)
class CreateServiceActivityCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    service_version_pk: int
    initiating_domain: InitiatingDomain
    initiating_domain_reference: str
    activity_basis_reference: str


@dataclass(frozen=True, slots=True)
class AssignServiceActivityCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    assignee_identity_id: UUID
    assignment_reference: str
    assignment_basis_reference: str


@dataclass(frozen=True, slots=True)
class AcceptServiceAssignmentCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID


@dataclass(frozen=True, slots=True)
class DeclineServiceAssignmentCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    decline_basis_reference: str


@dataclass(frozen=True, slots=True)
class SubmitServiceWorkCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    submission_reference: str
    submission_support_references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewServiceWorkCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    review_reference: str
    review_record_reference: str


@dataclass(frozen=True, slots=True)
class CompleteServiceActivityCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    completion_record_reference: str


@dataclass(frozen=True, slots=True)
class CancelServiceActivityCommand:
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    cancellation_basis_reference: str


@dataclass(frozen=True, slots=True)
class CreateServiceActivityResult:
    activity_id: UUID
    resulting_state: ServiceActivityState
    transition_id: int
    transition_sequence: int
    lineage_reference: str
    evidence: EvidenceResult
    authority_reference: str
    authority_decision_reference: str


@dataclass(frozen=True, slots=True)
class AssignServiceActivityResult:
    activity_id: UUID
    resulting_state: ServiceActivityState
    transition_id: int
    transition_sequence: int
    lineage_reference: str
    assignment_id: int
    evidence: EvidenceResult
    authority_reference: str
    authority_decision_reference: str


@dataclass(frozen=True, slots=True)
class AcceptServiceAssignmentResult:
    activity_id: UUID
    resulting_state: ServiceActivityState
    transition_id: int
    transition_sequence: int
    lineage_reference: str
    evidence: EvidenceResult
    authority_reference: str
    authority_decision_reference: str


@dataclass(frozen=True, slots=True)
class DeclineServiceAssignmentResult:
    activity_id: UUID
    resulting_state: ServiceActivityState
    transition_id: int
    transition_sequence: int
    lineage_reference: str
    evidence: EvidenceResult
    authority_reference: str
    authority_decision_reference: str


@dataclass(frozen=True, slots=True)
class SubmitServiceWorkResult:
    activity_id: UUID
    resulting_state: ServiceActivityState
    transition_id: int
    transition_sequence: int
    lineage_reference: str
    work_submission_id: int
    evidence: EvidenceResult
    authority_reference: str
    authority_decision_reference: str


@dataclass(frozen=True, slots=True)
class ReviewServiceWorkResult:
    activity_id: UUID
    resulting_state: ServiceActivityState
    transition_id: int
    transition_sequence: int
    lineage_reference: str
    service_activity_review_id: int
    evidence: EvidenceResult
    authority_reference: str
    authority_decision_reference: str


@dataclass(frozen=True, slots=True)
class CompleteServiceActivityResult:
    activity_id: UUID
    resulting_state: ServiceActivityState
    transition_id: int
    transition_sequence: int
    lineage_reference: str
    evidence: EvidenceResult
    authority_reference: str
    authority_decision_reference: str


@dataclass(frozen=True, slots=True)
class CancelServiceActivityResult:
    activity_id: UUID
    resulting_state: ServiceActivityState
    transition_id: int
    transition_sequence: int
    lineage_reference: str
    evidence: EvidenceResult
    authority_reference: str
    authority_decision_reference: str


def _canonical_bytes(obj: object) -> bytes:
    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _target_fingerprint(action: ServiceCommandAction, activity_id: UUID, **extras: object) -> str:
    target = {
        "action": action.value,
        "activity_id": str(activity_id),
        "schema": _TARGET_SCHEMA,
    }
    target.update(extras)
    digest = hashlib.sha256(_TARGET_DOMAIN + _canonical_bytes(target)).hexdigest()
    return digest


def _payload_fingerprint(
    action: ServiceCommandAction,
    actor_identity_id: UUID,
    actor_access_epoch: int,
    request_reference: str,
    idempotency_key: str,
    occurred_at: datetime,
    target: dict,
    command: dict,
    evidence: list[dict],
) -> str:
    payload = {
        "action": action.value,
        "actor_access_epoch": actor_access_epoch,
        "actor_identity_id": str(actor_identity_id),
        "command": command,
        "evidence": evidence,
        "idempotency_key": idempotency_key,
        "occurred_at": canonical_timestamp(occurred_at),
        "request_reference": request_reference,
        "schema": _COMMAND_SCHEMA,
        "target": target,
    }
    digest = hashlib.sha256(_COMMAND_DOMAIN + _canonical_bytes(payload)).hexdigest()
    return digest


def _lineage_reference(
    activity_id: UUID,
    sequence: int,
    action: ServiceCommandAction,
    actor_identity_id: UUID,
    actor_access_epoch: int,
    payload_fingerprint: str,
    occurred_at: datetime,
) -> str:
    lineage = {
        "action": action.value,
        "activity_id": str(activity_id),
        "actor_access_epoch": actor_access_epoch,
        "actor_identity_id": str(actor_identity_id),
        "occurred_at": canonical_timestamp(occurred_at),
        "payload_fingerprint": payload_fingerprint,
        "schema": _LINEAGE_SCHEMA,
        "sequence": sequence,
    }
    digest = hashlib.sha256(_LINEAGE_DOMAIN + _canonical_bytes(lineage)).hexdigest()
    return f"s012l1:{digest}"


def _validate_reference(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ServiceActivityValidationError(f"{name} must be a string")
    stripped = value.strip()
    if not stripped or len(stripped) > maximum:
        raise ServiceActivityValidationError(f"{name} must be 1..{maximum} non-empty characters")
    return stripped


def _validate_occurred_at(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ServiceActivityValidationError("occurred_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _sorted_evidence_dicts(evidence_tuples: list[tuple[str, str]], actor_identity_id: UUID) -> list[dict]:
    actor_str = str(actor_identity_id)
    entries = [
        {"evidence_kind": kind, "reference": ref, "supplied_by_identity_id": actor_str}
        for kind, ref in evidence_tuples
    ]
    entries.sort(key=lambda e: (e["evidence_kind"].encode("utf-8"), e["reference"].encode("utf-8")))
    return entries


def _evidence_result_from_rows(rows) -> EvidenceResult:
    items = [(ServiceActivityEvidenceKind(r.evidence_kind), r.reference) for r in rows]
    items.sort(key=lambda t: (t[0].value.encode("utf-8"), t[1].encode("utf-8")))
    return tuple(items)


def _is_postgresql(alias: str) -> bool:
    return "postgresql" in connections[alias].settings_dict.get("ENGINE", "")


class ServiceActivityService:
    def __init__(
        self,
        *,
        authority: ServiceCommandAuthority,
        clock: Callable[[], datetime],
        database_alias: str = "default",
    ) -> None:
        if not isinstance(authority, ServiceCommandAuthority):
            raise TypeError("authority must be a ServiceCommandAuthority")
        if authority.database_alias != database_alias:
            raise ValueError("authority database_alias mismatch")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not isinstance(database_alias, str) or not database_alias:
            raise ValueError("database_alias is required")
        self._authority = authority
        self._clock = clock
        self._alias = database_alias

    def _lock_actor(self, credential: User) -> Identity:
        try:
            identity = (
                Identity.objects.using(self._alias)
                .select_for_update()
                .select_related("credential")
                .get(credential=credential)
            )
        except Identity.DoesNotExist:
            raise ServiceActivityActorError("actor Identity not found")
        if identity.access_state != Identity.AccessState.ACTIVE:
            raise ServiceActivityActorError("actor Identity is not active")
        if not identity.credential.is_active:
            raise ServiceActivityActorError("actor credential is not active")
        return identity

    def _lock_remaining_identities(self, pks: set[int], actor_pk: int) -> dict[int, Identity]:
        remaining = sorted(pks - {actor_pk})
        if not remaining:
            return {}
        rows = list(
            Identity.objects.using(self._alias)
            .select_for_update()
            .select_related("credential")
            .filter(pk__in=remaining)
            .order_by("pk")
        )
        return {r.pk: r for r in rows}

    def _require_active_identity(self, identity: Identity, label: str) -> None:
        if identity.access_state != Identity.AccessState.ACTIVE:
            raise ServiceActivityActorError(f"{label} Identity is not active")
        if not identity.credential.is_active:
            raise ServiceActivityActorError(f"{label} credential is not active")

    def _qualify_authority(
        self,
        actor: Identity,
        action: ServiceCommandAction,
        target_fp: str,
        request_reference: str,
        idempotency_key: str,
    ):
        evaluated_at = _validate_occurred_at(self._clock())
        request = ServiceCommandAuthorityRequest(
            database_alias=self._alias,
            actor_pk=actor.pk,
            actor_identity_id=actor.identity_id,
            actor_access_epoch=actor.access_epoch,
            action=action,
            target_fingerprint=target_fp,
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            evaluated_at=evaluated_at,
        )
        return self._authority.qualify(request=request)

    def _check_replay(
        self,
        actor: Identity,
        action: ServiceCommandAction,
        idempotency_key: str,
        expected_epoch: int,
        expected_fingerprint: str,
    ):
        existing = (
            ServiceActivityTransition.objects.using(self._alias)
            .select_for_update()
            .filter(
                actor=actor,
                action=action.value,
                idempotency_key=idempotency_key,
            )
            .first()
        )
        return existing

    def _resolve_replay(self, existing: ServiceActivityTransition, expected_epoch: int, expected_fingerprint: str):
        if existing.actor_access_epoch != expected_epoch:
            raise ServiceActivityCrossEpochConflict("cross-epoch conflict")
        if existing.payload_fingerprint != expected_fingerprint:
            raise ServiceActivityPayloadConflict("payload conflict")

    def _write_evidence(self, transition, actor, authority_reference: str, occurred_at: datetime, evidence_tuples: list[tuple[str, str]]):
        rows = []
        for kind_value, ref in evidence_tuples:
            rows.append(ServiceActivityEvidenceReference(
                transition=transition,
                evidence_kind=kind_value,
                reference=ref,
                supplied_by=actor,
                authority_reference=authority_reference,
                occurred_at=occurred_at,
            ))
        for r in rows:
            r.save(using=self._alias)
        return rows

    def _reconstruct_evidence(self, transition) -> EvidenceResult:
        rows = list(
            ServiceActivityEvidenceReference.objects.using(self._alias)
            .filter(transition=transition)
        )
        for r in rows:
            if r.occurred_at != transition.occurred_at:
                raise ServiceActivityMalformedReplay("evidence occurrence time mismatch")
            if r.supplied_by_id != transition.actor_id:
                raise ServiceActivityMalformedReplay("evidence supplied_by mismatch")
        return _evidence_result_from_rows(rows)

    def _try_insert_with_savepoint(self, write_fn, replay_fn):
        if _is_postgresql(self._alias):
            try:
                with transaction.atomic(using=self._alias):
                    return write_fn()
            except IntegrityError as exc:
                cause = getattr(exc, "__cause__", None)
                diag = getattr(cause, "diag", None) if cause else None
                constraint_name = getattr(diag, "constraint_name", None) if diag else None
                if constraint_name == _IDEM_CONSTRAINT:
                    return replay_fn()
                raise
        else:
            return write_fn()

    def create_service_activity(self, command: CreateServiceActivityCommand) -> CreateServiceActivityResult:
        with transaction.atomic(using=self._alias):
            return self._execute_create(command)

    def _execute_create(self, command: CreateServiceActivityCommand) -> CreateServiceActivityResult:
        action = ServiceCommandAction.CREATE
        request_ref = _validate_reference(command.request_reference, "request_reference", 128)
        idem_key = _validate_reference(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_occurred_at(command.occurred_at)
        domain_ref = _validate_reference(command.initiating_domain_reference, "initiating_domain_reference", 255)
        basis_ref = _validate_reference(command.activity_basis_reference, "activity_basis_reference", 255)

        if not isinstance(command.activity_id, UUID):
            raise ServiceActivityValidationError("activity_id must be a UUID")
        if not isinstance(command.service_version_pk, int) or command.service_version_pk < 1:
            raise ServiceActivityValidationError("service_version_pk must be a positive integer")
        if not isinstance(command.initiating_domain, InitiatingDomain):
            raise ServiceActivityValidationError("initiating_domain is invalid")

        try:
            sv = ServiceVersion.objects.using(self._alias).select_related("service").get(pk=command.service_version_pk)
        except ServiceVersion.DoesNotExist:
            raise ServiceActivityNotFound("ServiceVersion not found")

        service = Service.objects.using(self._alias).select_for_update().get(pk=sv.service_id)
        ServiceVersion.objects.using(self._alias).select_for_update().get(pk=sv.pk)

        actor = self._lock_actor(command.credential)

        target_fp = _target_fingerprint(action, command.activity_id, service_version_pk=command.service_version_pk)
        decision = self._qualify_authority(actor, action, target_fp, request_ref, idem_key)

        evidence_tuples = [(ServiceActivityEvidenceKind.ACTIVITY_BASIS.value, basis_ref)]
        evidence_dicts = _sorted_evidence_dicts(evidence_tuples, actor.identity_id)
        target_dict = {"activity_id": str(command.activity_id), "service_version_pk": command.service_version_pk}
        command_dict = {
            "activity_basis_reference": basis_ref,
            "initiating_domain": command.initiating_domain.value,
            "initiating_domain_reference": domain_ref,
        }
        fingerprint = _payload_fingerprint(
            action, actor.identity_id, actor.access_epoch,
            request_ref, idem_key, occurred_at,
            target_dict, command_dict, evidence_dicts,
        )

        existing = self._check_replay(actor, action, idem_key, actor.access_epoch, fingerprint)
        if existing is not None:
            self._resolve_replay(existing, actor.access_epoch, fingerprint)
            return self._reconstruct_create(existing, command.activity_id)

        if service.state != Service.State.PUBLISHED:
            raise ServiceActivityLifecycleError("Service must be PUBLISHED")
        if service.current_version_id != sv.pk:
            raise ServiceActivityLifecycleError("ServiceVersion must be the current version")

        def _write():
            activity = ServiceActivity(
                activity_id=command.activity_id,
                service_version=sv,
                initiating_domain=command.initiating_domain.value,
                initiating_domain_reference=domain_ref,
                state=ServiceActivityState.UNASSIGNED.value,
                created_by=actor,
                created_at=occurred_at,
            )
            activity.save(using=self._alias)

            lineage_ref = _lineage_reference(
                command.activity_id, 1, action, actor.identity_id,
                actor.access_epoch, fingerprint, occurred_at,
            )

            trans = ServiceActivityTransition(
                activity=activity,
                sequence=1,
                previous_transition=None,
                action=action.value,
                from_state=None,
                to_state=ServiceActivityState.UNASSIGNED.value,
                actor=actor,
                actor_access_epoch=actor.access_epoch,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
                authority_evaluated_at=decision.evaluated_at,
                request_reference=request_ref,
                idempotency_key=idem_key,
                payload_fingerprint=fingerprint,
                occurred_at=occurred_at,
                lineage_reference=lineage_ref,
            )
            trans.save(using=self._alias)

            activity.head_transition = trans
            activity.save(using=self._alias)

            self._write_evidence(trans, actor, decision.authority_reference, occurred_at, evidence_tuples)

            ev_result = tuple((ServiceActivityEvidenceKind(k), r) for k, r in evidence_tuples)
            return CreateServiceActivityResult(
                activity_id=command.activity_id,
                resulting_state=ServiceActivityState.UNASSIGNED,
                transition_id=trans.pk,
                transition_sequence=1,
                lineage_reference=lineage_ref,
                evidence=ev_result,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
            )

        def _replay_after_race():
            race_winner = (
                ServiceActivityTransition.objects.using(self._alias)
                .select_for_update()
                .get(actor=actor, action=action.value, idempotency_key=idem_key)
            )
            self._resolve_replay(race_winner, actor.access_epoch, fingerprint)
            return self._reconstruct_create(race_winner, command.activity_id)

        return self._try_insert_with_savepoint(_write, _replay_after_race)

    def _reconstruct_create(self, trans: ServiceActivityTransition, activity_id: UUID) -> CreateServiceActivityResult:
        activity = ServiceActivity.objects.using(self._alias).get(pk=trans.activity_id)
        if activity.activity_id != activity_id:
            raise ServiceActivityMalformedReplay("activity_id mismatch")
        if activity.created_at != trans.occurred_at:
            raise ServiceActivityMalformedReplay("activity created_at mismatch")
        if trans.sequence != 1 or trans.previous_transition_id is not None:
            raise ServiceActivityMalformedReplay("CREATE must be initial transition")
        evidence = self._reconstruct_evidence(trans)
        if len(evidence) != 1 or evidence[0][0] != ServiceActivityEvidenceKind.ACTIVITY_BASIS:
            raise ServiceActivityMalformedReplay("CREATE evidence mismatch")
        return CreateServiceActivityResult(
            activity_id=activity.activity_id,
            resulting_state=ServiceActivityState(trans.to_state),
            transition_id=trans.pk,
            transition_sequence=trans.sequence,
            lineage_reference=trans.lineage_reference,
            evidence=evidence,
            authority_reference=trans.authority_reference,
            authority_decision_reference=trans.authority_decision_reference,
        )

    def assign_service_activity(self, command: AssignServiceActivityCommand) -> AssignServiceActivityResult:
        with transaction.atomic(using=self._alias):
            return self._execute_assign(command)

    def _execute_assign(self, command: AssignServiceActivityCommand) -> AssignServiceActivityResult:
        action = ServiceCommandAction.ASSIGN
        request_ref = _validate_reference(command.request_reference, "request_reference", 128)
        idem_key = _validate_reference(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_occurred_at(command.occurred_at)
        assign_ref = _validate_reference(command.assignment_reference, "assignment_reference", 255)
        basis_ref = _validate_reference(command.assignment_basis_reference, "assignment_basis_reference", 255)

        if not isinstance(command.activity_id, UUID):
            raise ServiceActivityValidationError("activity_id must be a UUID")
        if not isinstance(command.assignee_identity_id, UUID):
            raise ServiceActivityValidationError("assignee_identity_id must be a UUID")

        activity = (
            ServiceActivity.objects.using(self._alias)
            .select_for_update()
            .get(activity_id=command.activity_id)
        )

        actor = self._lock_actor(command.credential)

        try:
            assignee = (
                Identity.objects.using(self._alias)
                .select_for_update()
                .select_related("credential")
                .get(identity_id=command.assignee_identity_id)
            )
        except Identity.DoesNotExist:
            raise ServiceActivityActorError("assignee Identity not found")

        remaining_pks = {assignee.pk} - {actor.pk}
        if remaining_pks:
            self._lock_remaining_identities(remaining_pks | {actor.pk}, actor.pk)
        self._require_active_identity(assignee, "assignee")

        target_fp = _target_fingerprint(
            action, command.activity_id,
            assignee_identity_id=str(command.assignee_identity_id),
        )
        decision = self._qualify_authority(actor, action, target_fp, request_ref, idem_key)

        evidence_tuples = [(ServiceActivityEvidenceKind.ASSIGNMENT_BASIS.value, basis_ref)]
        evidence_dicts = _sorted_evidence_dicts(evidence_tuples, actor.identity_id)
        target_dict = {"activity_id": str(command.activity_id), "assignee_identity_id": str(command.assignee_identity_id)}
        command_dict = {
            "assignee_identity_id": str(command.assignee_identity_id),
            "assignment_basis_reference": basis_ref,
            "assignment_reference": assign_ref,
        }
        fingerprint = _payload_fingerprint(
            action, actor.identity_id, actor.access_epoch,
            request_ref, idem_key, occurred_at,
            target_dict, command_dict, evidence_dicts,
        )

        existing = self._check_replay(actor, action, idem_key, actor.access_epoch, fingerprint)
        if existing is not None:
            self._resolve_replay(existing, actor.access_epoch, fingerprint)
            return self._reconstruct_assign(existing, command.activity_id)

        if activity.state != ServiceActivityState.UNASSIGNED.value:
            raise ServiceActivityLifecycleError("Activity must be UNASSIGNED for ASSIGN")

        head = self._lock_head(activity)

        def _write():
            seq = (head.sequence if head else 0) + 1
            lineage_ref = _lineage_reference(
                command.activity_id, seq, action, actor.identity_id,
                actor.access_epoch, fingerprint, occurred_at,
            )
            trans = ServiceActivityTransition(
                activity=activity,
                sequence=seq,
                previous_transition=head,
                action=action.value,
                from_state=ServiceActivityState.UNASSIGNED.value,
                to_state=ServiceActivityState.ASSIGNED.value,
                actor=actor,
                actor_access_epoch=actor.access_epoch,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
                authority_evaluated_at=decision.evaluated_at,
                request_reference=request_ref,
                idempotency_key=idem_key,
                payload_fingerprint=fingerprint,
                occurred_at=occurred_at,
                lineage_reference=lineage_ref,
            )
            trans.save(using=self._alias)

            assignment = ServiceActivityAssignment(
                activity=activity,
                assignee=assignee,
                assigned_by=actor,
                assignment_reference=assign_ref,
                assigned_at=occurred_at,
                transition=trans,
            )
            assignment.save(using=self._alias)

            activity.state = ServiceActivityState.ASSIGNED.value
            activity.head_transition = trans
            activity.save(using=self._alias)

            self._write_evidence(trans, actor, decision.authority_reference, occurred_at, evidence_tuples)

            ev_result = tuple((ServiceActivityEvidenceKind(k), r) for k, r in evidence_tuples)
            return AssignServiceActivityResult(
                activity_id=command.activity_id,
                resulting_state=ServiceActivityState.ASSIGNED,
                transition_id=trans.pk,
                transition_sequence=seq,
                lineage_reference=lineage_ref,
                assignment_id=assignment.pk,
                evidence=ev_result,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
            )

        def _replay_after_race():
            race_winner = (
                ServiceActivityTransition.objects.using(self._alias)
                .select_for_update()
                .get(actor=actor, action=action.value, idempotency_key=idem_key)
            )
            self._resolve_replay(race_winner, actor.access_epoch, fingerprint)
            return self._reconstruct_assign(race_winner, command.activity_id)

        return self._try_insert_with_savepoint(_write, _replay_after_race)

    def _reconstruct_assign(self, trans: ServiceActivityTransition, activity_id: UUID) -> AssignServiceActivityResult:
        activity = ServiceActivity.objects.using(self._alias).get(pk=trans.activity_id)
        if activity.activity_id != activity_id:
            raise ServiceActivityMalformedReplay("activity_id mismatch")
        try:
            assignment = ServiceActivityAssignment.objects.using(self._alias).get(transition=trans)
        except ServiceActivityAssignment.DoesNotExist:
            raise ServiceActivityMalformedReplay("assignment row missing")
        if assignment.assigned_at != trans.occurred_at:
            raise ServiceActivityMalformedReplay("assignment occurrence time mismatch")
        evidence = self._reconstruct_evidence(trans)
        if len(evidence) != 1 or evidence[0][0] != ServiceActivityEvidenceKind.ASSIGNMENT_BASIS:
            raise ServiceActivityMalformedReplay("ASSIGN evidence mismatch")
        return AssignServiceActivityResult(
            activity_id=activity.activity_id,
            resulting_state=ServiceActivityState(trans.to_state),
            transition_id=trans.pk,
            transition_sequence=trans.sequence,
            lineage_reference=trans.lineage_reference,
            assignment_id=assignment.pk,
            evidence=evidence,
            authority_reference=trans.authority_reference,
            authority_decision_reference=trans.authority_decision_reference,
        )

    def accept_service_assignment(self, command: AcceptServiceAssignmentCommand) -> AcceptServiceAssignmentResult:
        with transaction.atomic(using=self._alias):
            return self._execute_accept(command)

    def _execute_accept(self, command: AcceptServiceAssignmentCommand) -> AcceptServiceAssignmentResult:
        action = ServiceCommandAction.ACCEPT_ASSIGNMENT
        request_ref = _validate_reference(command.request_reference, "request_reference", 128)
        idem_key = _validate_reference(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_occurred_at(command.occurred_at)

        if not isinstance(command.activity_id, UUID):
            raise ServiceActivityValidationError("activity_id must be a UUID")

        activity = (
            ServiceActivity.objects.using(self._alias)
            .select_for_update()
            .get(activity_id=command.activity_id)
        )

        actor = self._lock_actor(command.credential)

        assignment = self._get_assignment(activity)
        if assignment.assignee_id != actor.pk:
            raise ServiceActivityActorError("actor must be the assignee for ACCEPT")

        target_fp = _target_fingerprint(action, command.activity_id)
        decision = self._qualify_authority(actor, action, target_fp, request_ref, idem_key)

        evidence_tuples: list[tuple[str, str]] = []
        evidence_dicts = _sorted_evidence_dicts(evidence_tuples, actor.identity_id)
        target_dict = {"activity_id": str(command.activity_id)}
        command_dict: dict = {}
        fingerprint = _payload_fingerprint(
            action, actor.identity_id, actor.access_epoch,
            request_ref, idem_key, occurred_at,
            target_dict, command_dict, evidence_dicts,
        )

        existing = self._check_replay(actor, action, idem_key, actor.access_epoch, fingerprint)
        if existing is not None:
            self._resolve_replay(existing, actor.access_epoch, fingerprint)
            return self._reconstruct_accept(existing, command.activity_id)

        if activity.state != ServiceActivityState.ASSIGNED.value:
            raise ServiceActivityLifecycleError("Activity must be ASSIGNED for ACCEPT")

        head = self._lock_head(activity)

        def _write():
            seq = (head.sequence if head else 0) + 1
            lineage_ref = _lineage_reference(
                command.activity_id, seq, action, actor.identity_id,
                actor.access_epoch, fingerprint, occurred_at,
            )
            trans = ServiceActivityTransition(
                activity=activity,
                sequence=seq,
                previous_transition=head,
                action=action.value,
                from_state=ServiceActivityState.ASSIGNED.value,
                to_state=ServiceActivityState.IN_PROGRESS.value,
                actor=actor,
                actor_access_epoch=actor.access_epoch,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
                authority_evaluated_at=decision.evaluated_at,
                request_reference=request_ref,
                idempotency_key=idem_key,
                payload_fingerprint=fingerprint,
                occurred_at=occurred_at,
                lineage_reference=lineage_ref,
            )
            trans.save(using=self._alias)

            activity.state = ServiceActivityState.IN_PROGRESS.value
            activity.head_transition = trans
            activity.save(using=self._alias)

            return AcceptServiceAssignmentResult(
                activity_id=command.activity_id,
                resulting_state=ServiceActivityState.IN_PROGRESS,
                transition_id=trans.pk,
                transition_sequence=seq,
                lineage_reference=lineage_ref,
                evidence=(),
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
            )

        def _replay_after_race():
            race_winner = (
                ServiceActivityTransition.objects.using(self._alias)
                .select_for_update()
                .get(actor=actor, action=action.value, idempotency_key=idem_key)
            )
            self._resolve_replay(race_winner, actor.access_epoch, fingerprint)
            return self._reconstruct_accept(race_winner, command.activity_id)

        return self._try_insert_with_savepoint(_write, _replay_after_race)

    def _reconstruct_accept(self, trans: ServiceActivityTransition, activity_id: UUID) -> AcceptServiceAssignmentResult:
        activity = ServiceActivity.objects.using(self._alias).get(pk=trans.activity_id)
        if activity.activity_id != activity_id:
            raise ServiceActivityMalformedReplay("activity_id mismatch")
        evidence = self._reconstruct_evidence(trans)
        if len(evidence) != 0:
            raise ServiceActivityMalformedReplay("ACCEPT must have no evidence")
        return AcceptServiceAssignmentResult(
            activity_id=activity.activity_id,
            resulting_state=ServiceActivityState(trans.to_state),
            transition_id=trans.pk,
            transition_sequence=trans.sequence,
            lineage_reference=trans.lineage_reference,
            evidence=(),
            authority_reference=trans.authority_reference,
            authority_decision_reference=trans.authority_decision_reference,
        )

    def decline_service_assignment(self, command: DeclineServiceAssignmentCommand) -> DeclineServiceAssignmentResult:
        with transaction.atomic(using=self._alias):
            return self._execute_decline(command)

    def _execute_decline(self, command: DeclineServiceAssignmentCommand) -> DeclineServiceAssignmentResult:
        action = ServiceCommandAction.DECLINE_ASSIGNMENT
        request_ref = _validate_reference(command.request_reference, "request_reference", 128)
        idem_key = _validate_reference(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_occurred_at(command.occurred_at)
        decline_ref = _validate_reference(command.decline_basis_reference, "decline_basis_reference", 255)

        if not isinstance(command.activity_id, UUID):
            raise ServiceActivityValidationError("activity_id must be a UUID")

        activity = (
            ServiceActivity.objects.using(self._alias)
            .select_for_update()
            .get(activity_id=command.activity_id)
        )

        actor = self._lock_actor(command.credential)

        assignment = self._get_assignment(activity)
        if assignment.assignee_id != actor.pk:
            raise ServiceActivityActorError("actor must be the assignee for DECLINE")

        target_fp = _target_fingerprint(action, command.activity_id)
        decision = self._qualify_authority(actor, action, target_fp, request_ref, idem_key)

        evidence_tuples = [(ServiceActivityEvidenceKind.DECLINE_BASIS.value, decline_ref)]
        evidence_dicts = _sorted_evidence_dicts(evidence_tuples, actor.identity_id)
        target_dict = {"activity_id": str(command.activity_id)}
        command_dict = {"decline_basis_reference": decline_ref}
        fingerprint = _payload_fingerprint(
            action, actor.identity_id, actor.access_epoch,
            request_ref, idem_key, occurred_at,
            target_dict, command_dict, evidence_dicts,
        )

        existing = self._check_replay(actor, action, idem_key, actor.access_epoch, fingerprint)
        if existing is not None:
            self._resolve_replay(existing, actor.access_epoch, fingerprint)
            return self._reconstruct_decline(existing, command.activity_id)

        if activity.state != ServiceActivityState.ASSIGNED.value:
            raise ServiceActivityLifecycleError("Activity must be ASSIGNED for DECLINE")

        head = self._lock_head(activity)

        def _write():
            seq = (head.sequence if head else 0) + 1
            lineage_ref = _lineage_reference(
                command.activity_id, seq, action, actor.identity_id,
                actor.access_epoch, fingerprint, occurred_at,
            )
            trans = ServiceActivityTransition(
                activity=activity,
                sequence=seq,
                previous_transition=head,
                action=action.value,
                from_state=ServiceActivityState.ASSIGNED.value,
                to_state=ServiceActivityState.DECLINED.value,
                actor=actor,
                actor_access_epoch=actor.access_epoch,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
                authority_evaluated_at=decision.evaluated_at,
                request_reference=request_ref,
                idempotency_key=idem_key,
                payload_fingerprint=fingerprint,
                occurred_at=occurred_at,
                lineage_reference=lineage_ref,
            )
            trans.save(using=self._alias)

            activity.state = ServiceActivityState.DECLINED.value
            activity.head_transition = trans
            activity.save(using=self._alias)

            self._write_evidence(trans, actor, decision.authority_reference, occurred_at, evidence_tuples)

            ev_result = tuple((ServiceActivityEvidenceKind(k), r) for k, r in evidence_tuples)
            return DeclineServiceAssignmentResult(
                activity_id=command.activity_id,
                resulting_state=ServiceActivityState.DECLINED,
                transition_id=trans.pk,
                transition_sequence=seq,
                lineage_reference=lineage_ref,
                evidence=ev_result,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
            )

        def _replay_after_race():
            race_winner = (
                ServiceActivityTransition.objects.using(self._alias)
                .select_for_update()
                .get(actor=actor, action=action.value, idempotency_key=idem_key)
            )
            self._resolve_replay(race_winner, actor.access_epoch, fingerprint)
            return self._reconstruct_decline(race_winner, command.activity_id)

        return self._try_insert_with_savepoint(_write, _replay_after_race)

    def _reconstruct_decline(self, trans: ServiceActivityTransition, activity_id: UUID) -> DeclineServiceAssignmentResult:
        activity = ServiceActivity.objects.using(self._alias).get(pk=trans.activity_id)
        if activity.activity_id != activity_id:
            raise ServiceActivityMalformedReplay("activity_id mismatch")
        evidence = self._reconstruct_evidence(trans)
        if len(evidence) != 1 or evidence[0][0] != ServiceActivityEvidenceKind.DECLINE_BASIS:
            raise ServiceActivityMalformedReplay("DECLINE evidence mismatch")
        return DeclineServiceAssignmentResult(
            activity_id=activity.activity_id,
            resulting_state=ServiceActivityState(trans.to_state),
            transition_id=trans.pk,
            transition_sequence=trans.sequence,
            lineage_reference=trans.lineage_reference,
            evidence=evidence,
            authority_reference=trans.authority_reference,
            authority_decision_reference=trans.authority_decision_reference,
        )

    def submit_service_work(self, command: SubmitServiceWorkCommand) -> SubmitServiceWorkResult:
        with transaction.atomic(using=self._alias):
            return self._execute_submit(command)

    def _execute_submit(self, command: SubmitServiceWorkCommand) -> SubmitServiceWorkResult:
        action = ServiceCommandAction.SUBMIT_WORK
        request_ref = _validate_reference(command.request_reference, "request_reference", 128)
        idem_key = _validate_reference(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_occurred_at(command.occurred_at)
        sub_ref = _validate_reference(command.submission_reference, "submission_reference", 255)

        if not isinstance(command.activity_id, UUID):
            raise ServiceActivityValidationError("activity_id must be a UUID")
        if not isinstance(command.submission_support_references, tuple):
            raise ServiceActivityValidationError("submission_support_references must be a tuple")

        support_refs = []
        seen_refs: set[str] = set()
        for i, ref in enumerate(command.submission_support_references):
            validated = _validate_reference(ref, f"submission_support_references[{i}]", 255)
            if validated in seen_refs:
                raise ServiceActivityValidationError("duplicate submission_support_reference")
            seen_refs.add(validated)
            support_refs.append(validated)

        activity = (
            ServiceActivity.objects.using(self._alias)
            .select_for_update()
            .get(activity_id=command.activity_id)
        )

        actor = self._lock_actor(command.credential)

        assignment = self._get_assignment(activity)
        if assignment.assignee_id != actor.pk:
            raise ServiceActivityActorError("actor must be the assignee for SUBMIT")

        target_fp = _target_fingerprint(action, command.activity_id)
        decision = self._qualify_authority(actor, action, target_fp, request_ref, idem_key)

        evidence_tuples = [(ServiceActivityEvidenceKind.SUBMISSION_SUPPORT.value, r) for r in support_refs]
        evidence_dicts = _sorted_evidence_dicts(evidence_tuples, actor.identity_id)
        target_dict = {"activity_id": str(command.activity_id)}
        command_dict = {
            "submission_reference": sub_ref,
            "submission_support_references": list(support_refs),
        }
        fingerprint = _payload_fingerprint(
            action, actor.identity_id, actor.access_epoch,
            request_ref, idem_key, occurred_at,
            target_dict, command_dict, evidence_dicts,
        )

        existing = self._check_replay(actor, action, idem_key, actor.access_epoch, fingerprint)
        if existing is not None:
            self._resolve_replay(existing, actor.access_epoch, fingerprint)
            return self._reconstruct_submit(existing, command.activity_id)

        if activity.state != ServiceActivityState.IN_PROGRESS.value:
            raise ServiceActivityLifecycleError("Activity must be IN_PROGRESS for SUBMIT")

        head = self._lock_head(activity)

        def _write():
            seq = (head.sequence if head else 0) + 1
            lineage_ref = _lineage_reference(
                command.activity_id, seq, action, actor.identity_id,
                actor.access_epoch, fingerprint, occurred_at,
            )
            trans = ServiceActivityTransition(
                activity=activity,
                sequence=seq,
                previous_transition=head,
                action=action.value,
                from_state=ServiceActivityState.IN_PROGRESS.value,
                to_state=ServiceActivityState.SUBMITTED.value,
                actor=actor,
                actor_access_epoch=actor.access_epoch,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
                authority_evaluated_at=decision.evaluated_at,
                request_reference=request_ref,
                idempotency_key=idem_key,
                payload_fingerprint=fingerprint,
                occurred_at=occurred_at,
                lineage_reference=lineage_ref,
            )
            trans.save(using=self._alias)

            submission = ServiceWorkSubmission(
                activity=activity,
                submitted_by=actor,
                submission_reference=sub_ref,
                submitted_at=occurred_at,
                transition=trans,
            )
            submission.save(using=self._alias)

            activity.state = ServiceActivityState.SUBMITTED.value
            activity.head_transition = trans
            activity.save(using=self._alias)

            self._write_evidence(trans, actor, decision.authority_reference, occurred_at, evidence_tuples)

            ev_result = _evidence_result_from_rows(
                ServiceActivityEvidenceReference.objects.using(self._alias).filter(transition=trans)
            )
            return SubmitServiceWorkResult(
                activity_id=command.activity_id,
                resulting_state=ServiceActivityState.SUBMITTED,
                transition_id=trans.pk,
                transition_sequence=seq,
                lineage_reference=lineage_ref,
                work_submission_id=submission.pk,
                evidence=ev_result,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
            )

        def _replay_after_race():
            race_winner = (
                ServiceActivityTransition.objects.using(self._alias)
                .select_for_update()
                .get(actor=actor, action=action.value, idempotency_key=idem_key)
            )
            self._resolve_replay(race_winner, actor.access_epoch, fingerprint)
            return self._reconstruct_submit(race_winner, command.activity_id)

        return self._try_insert_with_savepoint(_write, _replay_after_race)

    def _reconstruct_submit(self, trans: ServiceActivityTransition, activity_id: UUID) -> SubmitServiceWorkResult:
        activity = ServiceActivity.objects.using(self._alias).get(pk=trans.activity_id)
        if activity.activity_id != activity_id:
            raise ServiceActivityMalformedReplay("activity_id mismatch")
        try:
            submission = ServiceWorkSubmission.objects.using(self._alias).get(transition=trans)
        except ServiceWorkSubmission.DoesNotExist:
            raise ServiceActivityMalformedReplay("work submission row missing")
        if submission.submitted_at != trans.occurred_at:
            raise ServiceActivityMalformedReplay("submission occurrence time mismatch")
        evidence = self._reconstruct_evidence(trans)
        for kind, _ in evidence:
            if kind != ServiceActivityEvidenceKind.SUBMISSION_SUPPORT:
                raise ServiceActivityMalformedReplay("SUBMIT evidence kind mismatch")
        return SubmitServiceWorkResult(
            activity_id=activity.activity_id,
            resulting_state=ServiceActivityState(trans.to_state),
            transition_id=trans.pk,
            transition_sequence=trans.sequence,
            lineage_reference=trans.lineage_reference,
            work_submission_id=submission.pk,
            evidence=evidence,
            authority_reference=trans.authority_reference,
            authority_decision_reference=trans.authority_decision_reference,
        )

    def review_service_work(self, command: ReviewServiceWorkCommand) -> ReviewServiceWorkResult:
        with transaction.atomic(using=self._alias):
            return self._execute_review(command)

    def _execute_review(self, command: ReviewServiceWorkCommand) -> ReviewServiceWorkResult:
        action = ServiceCommandAction.REVIEW_WORK
        request_ref = _validate_reference(command.request_reference, "request_reference", 128)
        idem_key = _validate_reference(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_occurred_at(command.occurred_at)
        review_ref = _validate_reference(command.review_reference, "review_reference", 255)
        record_ref = _validate_reference(command.review_record_reference, "review_record_reference", 255)

        if not isinstance(command.activity_id, UUID):
            raise ServiceActivityValidationError("activity_id must be a UUID")

        activity = (
            ServiceActivity.objects.using(self._alias)
            .select_for_update()
            .get(activity_id=command.activity_id)
        )

        actor = self._lock_actor(command.credential)

        assignment = self._get_assignment(activity)
        identity_pks = {assignment.assignee_id, activity.created_by_id}
        self._lock_remaining_identities(identity_pks, actor.pk)

        target_fp = _target_fingerprint(action, command.activity_id)
        decision = self._qualify_authority(actor, action, target_fp, request_ref, idem_key)

        evidence_tuples = [(ServiceActivityEvidenceKind.REVIEW_RECORD.value, record_ref)]
        evidence_dicts = _sorted_evidence_dicts(evidence_tuples, actor.identity_id)
        target_dict = {"activity_id": str(command.activity_id)}
        command_dict = {
            "review_record_reference": record_ref,
            "review_reference": review_ref,
        }
        fingerprint = _payload_fingerprint(
            action, actor.identity_id, actor.access_epoch,
            request_ref, idem_key, occurred_at,
            target_dict, command_dict, evidence_dicts,
        )

        existing = self._check_replay(actor, action, idem_key, actor.access_epoch, fingerprint)
        if existing is not None:
            self._resolve_replay(existing, actor.access_epoch, fingerprint)
            return self._reconstruct_review(existing, command.activity_id)

        if activity.state != ServiceActivityState.SUBMITTED.value:
            raise ServiceActivityLifecycleError("Activity must be SUBMITTED for REVIEW")

        head = self._lock_head(activity)
        submission = self._get_submission(activity)

        def _write():
            seq = (head.sequence if head else 0) + 1
            lineage_ref = _lineage_reference(
                command.activity_id, seq, action, actor.identity_id,
                actor.access_epoch, fingerprint, occurred_at,
            )
            trans = ServiceActivityTransition(
                activity=activity,
                sequence=seq,
                previous_transition=head,
                action=action.value,
                from_state=ServiceActivityState.SUBMITTED.value,
                to_state=ServiceActivityState.REVIEWED.value,
                actor=actor,
                actor_access_epoch=actor.access_epoch,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
                authority_evaluated_at=decision.evaluated_at,
                request_reference=request_ref,
                idempotency_key=idem_key,
                payload_fingerprint=fingerprint,
                occurred_at=occurred_at,
                lineage_reference=lineage_ref,
            )
            trans.save(using=self._alias)

            review = ServiceActivityReview(
                submission=submission,
                reviewed_by=actor,
                review_reference=review_ref,
                reviewed_at=occurred_at,
                transition=trans,
            )
            review.save(using=self._alias)

            activity.state = ServiceActivityState.REVIEWED.value
            activity.head_transition = trans
            activity.save(using=self._alias)

            self._write_evidence(trans, actor, decision.authority_reference, occurred_at, evidence_tuples)

            ev_result = tuple((ServiceActivityEvidenceKind(k), r) for k, r in evidence_tuples)
            return ReviewServiceWorkResult(
                activity_id=command.activity_id,
                resulting_state=ServiceActivityState.REVIEWED,
                transition_id=trans.pk,
                transition_sequence=seq,
                lineage_reference=lineage_ref,
                service_activity_review_id=review.pk,
                evidence=ev_result,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
            )

        def _replay_after_race():
            race_winner = (
                ServiceActivityTransition.objects.using(self._alias)
                .select_for_update()
                .get(actor=actor, action=action.value, idempotency_key=idem_key)
            )
            self._resolve_replay(race_winner, actor.access_epoch, fingerprint)
            return self._reconstruct_review(race_winner, command.activity_id)

        return self._try_insert_with_savepoint(_write, _replay_after_race)

    def _reconstruct_review(self, trans: ServiceActivityTransition, activity_id: UUID) -> ReviewServiceWorkResult:
        activity = ServiceActivity.objects.using(self._alias).get(pk=trans.activity_id)
        if activity.activity_id != activity_id:
            raise ServiceActivityMalformedReplay("activity_id mismatch")
        try:
            review = ServiceActivityReview.objects.using(self._alias).get(transition=trans)
        except ServiceActivityReview.DoesNotExist:
            raise ServiceActivityMalformedReplay("review row missing")
        if review.reviewed_at != trans.occurred_at:
            raise ServiceActivityMalformedReplay("review occurrence time mismatch")
        evidence = self._reconstruct_evidence(trans)
        if len(evidence) != 1 or evidence[0][0] != ServiceActivityEvidenceKind.REVIEW_RECORD:
            raise ServiceActivityMalformedReplay("REVIEW evidence mismatch")
        return ReviewServiceWorkResult(
            activity_id=activity.activity_id,
            resulting_state=ServiceActivityState(trans.to_state),
            transition_id=trans.pk,
            transition_sequence=trans.sequence,
            lineage_reference=trans.lineage_reference,
            service_activity_review_id=review.pk,
            evidence=evidence,
            authority_reference=trans.authority_reference,
            authority_decision_reference=trans.authority_decision_reference,
        )

    def complete_service_activity(self, command: CompleteServiceActivityCommand) -> CompleteServiceActivityResult:
        with transaction.atomic(using=self._alias):
            return self._execute_complete(command)

    def _execute_complete(self, command: CompleteServiceActivityCommand) -> CompleteServiceActivityResult:
        action = ServiceCommandAction.COMPLETE_ACTIVITY
        request_ref = _validate_reference(command.request_reference, "request_reference", 128)
        idem_key = _validate_reference(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_occurred_at(command.occurred_at)
        completion_ref = _validate_reference(command.completion_record_reference, "completion_record_reference", 255)

        if not isinstance(command.activity_id, UUID):
            raise ServiceActivityValidationError("activity_id must be a UUID")

        activity = (
            ServiceActivity.objects.using(self._alias)
            .select_for_update()
            .get(activity_id=command.activity_id)
        )

        actor = self._lock_actor(command.credential)

        assignment = self._get_assignment(activity)
        submission = self._get_submission(activity)
        review = self._get_review(submission)
        identity_pks = {assignment.assignee_id, activity.created_by_id, review.reviewed_by_id}
        self._lock_remaining_identities(identity_pks, actor.pk)

        target_fp = _target_fingerprint(action, command.activity_id)
        decision = self._qualify_authority(actor, action, target_fp, request_ref, idem_key)

        evidence_tuples = [(ServiceActivityEvidenceKind.COMPLETION_RECORD.value, completion_ref)]
        evidence_dicts = _sorted_evidence_dicts(evidence_tuples, actor.identity_id)
        target_dict = {"activity_id": str(command.activity_id)}
        command_dict = {"completion_record_reference": completion_ref}
        fingerprint = _payload_fingerprint(
            action, actor.identity_id, actor.access_epoch,
            request_ref, idem_key, occurred_at,
            target_dict, command_dict, evidence_dicts,
        )

        existing = self._check_replay(actor, action, idem_key, actor.access_epoch, fingerprint)
        if existing is not None:
            self._resolve_replay(existing, actor.access_epoch, fingerprint)
            return self._reconstruct_complete(existing, command.activity_id)

        if activity.state != ServiceActivityState.REVIEWED.value:
            raise ServiceActivityLifecycleError("Activity must be REVIEWED for COMPLETE")

        head = self._lock_head(activity)

        def _write():
            seq = (head.sequence if head else 0) + 1
            lineage_ref = _lineage_reference(
                command.activity_id, seq, action, actor.identity_id,
                actor.access_epoch, fingerprint, occurred_at,
            )
            trans = ServiceActivityTransition(
                activity=activity,
                sequence=seq,
                previous_transition=head,
                action=action.value,
                from_state=ServiceActivityState.REVIEWED.value,
                to_state=ServiceActivityState.COMPLETED.value,
                actor=actor,
                actor_access_epoch=actor.access_epoch,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
                authority_evaluated_at=decision.evaluated_at,
                request_reference=request_ref,
                idempotency_key=idem_key,
                payload_fingerprint=fingerprint,
                occurred_at=occurred_at,
                lineage_reference=lineage_ref,
            )
            trans.save(using=self._alias)

            activity.state = ServiceActivityState.COMPLETED.value
            activity.head_transition = trans
            activity.save(using=self._alias)

            self._write_evidence(trans, actor, decision.authority_reference, occurred_at, evidence_tuples)

            ev_result = tuple((ServiceActivityEvidenceKind(k), r) for k, r in evidence_tuples)
            return CompleteServiceActivityResult(
                activity_id=command.activity_id,
                resulting_state=ServiceActivityState.COMPLETED,
                transition_id=trans.pk,
                transition_sequence=seq,
                lineage_reference=lineage_ref,
                evidence=ev_result,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
            )

        def _replay_after_race():
            race_winner = (
                ServiceActivityTransition.objects.using(self._alias)
                .select_for_update()
                .get(actor=actor, action=action.value, idempotency_key=idem_key)
            )
            self._resolve_replay(race_winner, actor.access_epoch, fingerprint)
            return self._reconstruct_complete(race_winner, command.activity_id)

        return self._try_insert_with_savepoint(_write, _replay_after_race)

    def _reconstruct_complete(self, trans: ServiceActivityTransition, activity_id: UUID) -> CompleteServiceActivityResult:
        activity = ServiceActivity.objects.using(self._alias).get(pk=trans.activity_id)
        if activity.activity_id != activity_id:
            raise ServiceActivityMalformedReplay("activity_id mismatch")
        evidence = self._reconstruct_evidence(trans)
        if len(evidence) != 1 or evidence[0][0] != ServiceActivityEvidenceKind.COMPLETION_RECORD:
            raise ServiceActivityMalformedReplay("COMPLETE evidence mismatch")
        return CompleteServiceActivityResult(
            activity_id=activity.activity_id,
            resulting_state=ServiceActivityState(trans.to_state),
            transition_id=trans.pk,
            transition_sequence=trans.sequence,
            lineage_reference=trans.lineage_reference,
            evidence=evidence,
            authority_reference=trans.authority_reference,
            authority_decision_reference=trans.authority_decision_reference,
        )

    def cancel_service_activity(self, command: CancelServiceActivityCommand) -> CancelServiceActivityResult:
        with transaction.atomic(using=self._alias):
            return self._execute_cancel(command)

    def _execute_cancel(self, command: CancelServiceActivityCommand) -> CancelServiceActivityResult:
        action = ServiceCommandAction.CANCEL_ACTIVITY
        request_ref = _validate_reference(command.request_reference, "request_reference", 128)
        idem_key = _validate_reference(command.idempotency_key, "idempotency_key", 120)
        occurred_at = _validate_occurred_at(command.occurred_at)
        cancel_ref = _validate_reference(command.cancellation_basis_reference, "cancellation_basis_reference", 255)

        if not isinstance(command.activity_id, UUID):
            raise ServiceActivityValidationError("activity_id must be a UUID")

        activity = (
            ServiceActivity.objects.using(self._alias)
            .select_for_update()
            .get(activity_id=command.activity_id)
        )

        actor = self._lock_actor(command.credential)

        identity_pks: set[int] = {activity.created_by_id}
        assignment = (
            ServiceActivityAssignment.objects.using(self._alias)
            .filter(activity=activity)
            .first()
        )
        if assignment:
            identity_pks.add(assignment.assignee_id)
        submission = (
            ServiceWorkSubmission.objects.using(self._alias)
            .filter(activity=activity)
            .first()
        )
        if submission:
            review = (
                ServiceActivityReview.objects.using(self._alias)
                .filter(submission=submission)
                .first()
            )
            if review:
                identity_pks.add(review.reviewed_by_id)
        self._lock_remaining_identities(identity_pks, actor.pk)

        target_fp = _target_fingerprint(action, command.activity_id)
        decision = self._qualify_authority(actor, action, target_fp, request_ref, idem_key)

        evidence_tuples = [(ServiceActivityEvidenceKind.CANCELLATION_BASIS.value, cancel_ref)]
        evidence_dicts = _sorted_evidence_dicts(evidence_tuples, actor.identity_id)
        target_dict = {"activity_id": str(command.activity_id)}
        command_dict = {"cancellation_basis_reference": cancel_ref}
        fingerprint = _payload_fingerprint(
            action, actor.identity_id, actor.access_epoch,
            request_ref, idem_key, occurred_at,
            target_dict, command_dict, evidence_dicts,
        )

        existing = self._check_replay(actor, action, idem_key, actor.access_epoch, fingerprint)
        if existing is not None:
            self._resolve_replay(existing, actor.access_epoch, fingerprint)
            return self._reconstruct_cancel(existing, command.activity_id)

        current_state = ServiceActivityState(activity.state)
        if current_state not in CANCEL_SOURCES:
            raise ServiceActivityLifecycleError("Activity state does not permit CANCEL")

        head = self._lock_head(activity)

        def _write():
            seq = (head.sequence if head else 0) + 1
            lineage_ref = _lineage_reference(
                command.activity_id, seq, action, actor.identity_id,
                actor.access_epoch, fingerprint, occurred_at,
            )
            trans = ServiceActivityTransition(
                activity=activity,
                sequence=seq,
                previous_transition=head,
                action=action.value,
                from_state=activity.state,
                to_state=ServiceActivityState.CANCELLED.value,
                actor=actor,
                actor_access_epoch=actor.access_epoch,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
                authority_evaluated_at=decision.evaluated_at,
                request_reference=request_ref,
                idempotency_key=idem_key,
                payload_fingerprint=fingerprint,
                occurred_at=occurred_at,
                lineage_reference=lineage_ref,
            )
            trans.save(using=self._alias)

            activity.state = ServiceActivityState.CANCELLED.value
            activity.head_transition = trans
            activity.save(using=self._alias)

            self._write_evidence(trans, actor, decision.authority_reference, occurred_at, evidence_tuples)

            ev_result = tuple((ServiceActivityEvidenceKind(k), r) for k, r in evidence_tuples)
            return CancelServiceActivityResult(
                activity_id=command.activity_id,
                resulting_state=ServiceActivityState.CANCELLED,
                transition_id=trans.pk,
                transition_sequence=seq,
                lineage_reference=lineage_ref,
                evidence=ev_result,
                authority_reference=decision.authority_reference,
                authority_decision_reference=decision.decision_reference,
            )

        def _replay_after_race():
            race_winner = (
                ServiceActivityTransition.objects.using(self._alias)
                .select_for_update()
                .get(actor=actor, action=action.value, idempotency_key=idem_key)
            )
            self._resolve_replay(race_winner, actor.access_epoch, fingerprint)
            return self._reconstruct_cancel(race_winner, command.activity_id)

        return self._try_insert_with_savepoint(_write, _replay_after_race)

    def _reconstruct_cancel(self, trans: ServiceActivityTransition, activity_id: UUID) -> CancelServiceActivityResult:
        activity = ServiceActivity.objects.using(self._alias).get(pk=trans.activity_id)
        if activity.activity_id != activity_id:
            raise ServiceActivityMalformedReplay("activity_id mismatch")
        evidence = self._reconstruct_evidence(trans)
        if len(evidence) != 1 or evidence[0][0] != ServiceActivityEvidenceKind.CANCELLATION_BASIS:
            raise ServiceActivityMalformedReplay("CANCEL evidence mismatch")
        return CancelServiceActivityResult(
            activity_id=activity.activity_id,
            resulting_state=ServiceActivityState(trans.to_state),
            transition_id=trans.pk,
            transition_sequence=trans.sequence,
            lineage_reference=trans.lineage_reference,
            evidence=evidence,
            authority_reference=trans.authority_reference,
            authority_decision_reference=trans.authority_decision_reference,
        )

    def _lock_head(self, activity: ServiceActivity):
        if activity.head_transition_id is None:
            return None
        return (
            ServiceActivityTransition.objects.using(self._alias)
            .select_for_update()
            .get(pk=activity.head_transition_id)
        )

    def _get_assignment(self, activity: ServiceActivity) -> ServiceActivityAssignment:
        try:
            return (
                ServiceActivityAssignment.objects.using(self._alias)
                .select_for_update()
                .get(activity=activity)
            )
        except ServiceActivityAssignment.DoesNotExist:
            raise ServiceActivityLifecycleError("no assignment exists for this Activity")

    def _get_submission(self, activity: ServiceActivity) -> ServiceWorkSubmission:
        try:
            return (
                ServiceWorkSubmission.objects.using(self._alias)
                .select_for_update()
                .get(activity=activity)
            )
        except ServiceWorkSubmission.DoesNotExist:
            raise ServiceActivityLifecycleError("no work submission exists for this Activity")

    def _get_review(self, submission: ServiceWorkSubmission) -> ServiceActivityReview:
        try:
            return (
                ServiceActivityReview.objects.using(self._alias)
                .select_for_update()
                .get(submission=submission)
            )
        except ServiceActivityReview.DoesNotExist:
            raise ServiceActivityLifecycleError("no review exists for this Activity")


__all__ = [
    "AcceptServiceAssignmentCommand",
    "AcceptServiceAssignmentResult",
    "AssignServiceActivityCommand",
    "AssignServiceActivityResult",
    "CancelServiceActivityCommand",
    "CancelServiceActivityResult",
    "CompleteServiceActivityCommand",
    "CompleteServiceActivityResult",
    "CreateServiceActivityCommand",
    "CreateServiceActivityResult",
    "DeclineServiceAssignmentCommand",
    "DeclineServiceAssignmentResult",
    "EvidenceResult",
    "InitiatingDomain",
    "ReviewServiceWorkCommand",
    "ReviewServiceWorkResult",
    "ServiceActivityCommandError",
    "ServiceActivityConflict",
    "ServiceActivityCrossEpochConflict",
    "ServiceActivityEvidenceKind",
    "ServiceActivityLifecycleError",
    "ServiceActivityMalformedReplay",
    "ServiceActivityNotFound",
    "ServiceActivityPayloadConflict",
    "ServiceActivityService",
    "ServiceActivityState",
    "ServiceActivityValidationError",
    "ServiceActivityActorError",
    "ServiceCommandAction",
    "SubmitServiceWorkCommand",
    "SubmitServiceWorkResult",
]
