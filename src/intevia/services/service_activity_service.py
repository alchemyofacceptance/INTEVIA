"""Command service for Service Activity orchestration.

First execution of an authorised command records a lifecycle transition
and its evidence references. The lifecycle is CREATE to UNASSIGNED;
ASSIGN from UNASSIGNED to ASSIGNED; ACCEPT from ASSIGNED to IN_PROGRESS
or DECLINE from ASSIGNED to DECLINED; SUBMIT from IN_PROGRESS to SUBMITTED;
REVIEW from SUBMITTED to REVIEWED; COMPLETE from REVIEWED to COMPLETED;
and CANCEL from any non-terminal state to CANCELLED.

Each public command enters an atomic block on the service's database
alias, requests locks for the actor's Identity and credential, and
qualifies authority through the injected ServiceCommandAuthority.
Replay is looked up by actor, action and idempotency key. Epoch or payload
fingerprint mismatches are refused; a matching replay reconstructs a
result from stored records and retains their authority references rather
than appending another transition.

Activities preserve their initiating domain and opaque references.
These records describe lifecycle occurrences, not work quality, approval
or value; referenced external content is not fetched or interpreted.
"""

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
    """Base class for the service-specific command errors defined in this module.

    Not raised directly in this module.
    """
    pass


class ServiceActivityConflict(ServiceActivityCommandError):
    """Base class for replay conflicts and inconsistent stored replay data.

    Not raised directly in this module; see its three subclasses.
    """
    pass


class ServiceActivityCrossEpochConflict(ServiceActivityConflict):
    """The same actor, action and idempotency key were first used under a different access epoch."""
    pass


class ServiceActivityPayloadConflict(ServiceActivityConflict):
    """The same actor, action and idempotency key arrived with a different payload fingerprint."""
    pass


class ServiceActivityMalformedReplay(ServiceActivityConflict):
    """A replay reconstruction check found missing or inconsistent stored data.

    Examples include missing related records, an Activity identifier mismatch,
    or evidence whose occurrence time, supplier or kind does not match the transition.
    """
    pass


class ServiceActivityLifecycleError(ServiceActivityCommandError):
    """The command's lifecycle requirements are not met.

    Covers an unpublished Service, a non-current ServiceVersion, an Activity in
    the wrong source state, or a missing required assignment, submission or review.
    """
    pass


class ServiceActivityValidationError(ServiceActivityCommandError):
    """A command value fails the service's validation checks.

    Examples include invalid references or identifiers, a timestamp without a
    timezone, an invalid initiating domain, or duplicate submission-support references.
    """
    pass


class ServiceActivityNotFound(ServiceActivityCommandError):
    """The ServiceVersion named by a create command does not exist."""
    pass


class ServiceActivityActorError(ServiceActivityCommandError):
    """The actor or assignee is missing or inactive, including an inactive credential.

    Also raised when the actor is not the assignee required to accept or decline
    an assignment, or to submit work.
    """
    pass


@dataclass(frozen=True, slots=True)
class CreateServiceActivityCommand:
    """Input to create an Activity for a ServiceVersion.

    The caller supplies the new activity_id. The references are validated by the service, not here.
    """
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
    """Input to assign an unassigned Activity to an Identity, with the assignment's reference and basis."""
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
    """Input for the assignee to accept their assignment."""
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID


@dataclass(frozen=True, slots=True)
class DeclineServiceAssignmentCommand:
    """Input for the assignee to decline their assignment, with the basis for declining."""
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    decline_basis_reference: str


@dataclass(frozen=True, slots=True)
class SubmitServiceWorkCommand:
    """Input to submit work on an Activity in progress, with the submission reference and its supporting references."""
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    submission_reference: str
    submission_support_references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewServiceWorkCommand:
    """Input to review submitted work, with the review reference and the reference to its record."""
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    review_reference: str
    review_record_reference: str


@dataclass(frozen=True, slots=True)
class CompleteServiceActivityCommand:
    """Input to complete a reviewed Activity, with the reference to the completion record."""
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    completion_record_reference: str


@dataclass(frozen=True, slots=True)
class CancelServiceActivityCommand:
    """Input to cancel an Activity that is not yet in a terminal state, with the basis for cancelling."""
    credential: User
    request_reference: str
    idempotency_key: str
    occurred_at: datetime
    activity_id: UUID
    cancellation_basis_reference: str


@dataclass(frozen=True, slots=True)
class CreateServiceActivityResult:
    """Outcome of creating an Activity.

    Carries the activity ID, resulting state, transition ID, sequence and lineage
    reference, evidence, and authority references.

    Returned for a first execution and for a matching replay alike.
    """
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
    """Outcome of assigning an Activity, including the assignment ID.

    Carries the activity ID, resulting state, transition ID, sequence and lineage
    reference, evidence, and authority references.
    """
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
    """Outcome of accepting an assignment.

    Carries the activity ID, resulting state, transition ID, sequence and lineage
    reference, and authority references. The evidence field is an empty tuple.
    """
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
    """Outcome of declining an assignment.

    Carries the activity ID, resulting state, transition ID, sequence and lineage
    reference, evidence, and authority references.
    """
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
    """Outcome of submitting work, including the work submission ID.

    Carries the activity ID, resulting state, transition ID, sequence and lineage
    reference, evidence, and authority references.
    """
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
    """Outcome of reviewing work, including the service activity review ID.

    Carries the activity ID, resulting state, transition ID, sequence and lineage
    reference, evidence, and authority references.
    """
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
    """Outcome of completing an Activity.

    Carries the activity ID, resulting state, transition ID, sequence and lineage
    reference, evidence, and authority references.
    """
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
    """Outcome of cancelling an Activity.

    Carries the activity ID, resulting state, transition ID, sequence and lineage
    reference, evidence, and authority references.
    """
    activity_id: UUID
    resulting_state: ServiceActivityState
    transition_id: int
    transition_sequence: int
    lineage_reference: str
    evidence: EvidenceResult
    authority_reference: str
    authority_decision_reference: str


def _canonical_bytes(obj: object) -> bytes:
    """Encode an object as the canonical JSON bytes the fingerprints hash.

    Use UTF-8 JSON with sorted keys, compact separators and unescaped
    non-ASCII characters. NaN and infinite floats raise ValueError.
    Unsupported object types raise TypeError; other serialization and UTF-8
    encoding errors also propagate.
    """
    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _target_fingerprint(action: ServiceCommandAction, activity_id: UUID, **extras: object) -> str:
    """Return the authority target fingerprint for an action on an Activity.

    The target holds the action, the activity ID as a string and the target
    schema, updated with any extra fields the caller passes (ASSIGN passes
    the assignee's identity ID). Return the hex SHA-256 of the target domain
    tag followed by the target's canonical bytes.
    """
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
    """Return the command payload fingerprint compared on replay.

    The payload holds the action, the actor's identity ID and access epoch,
    the request reference, the idempotency key, the occurrence time in
    canonical UTC form, the target, the command fields and the evidence
    entries, with the command schema. Return the hex SHA-256 of the command
    domain tag followed by the payload's canonical bytes. Lists among the
    command fields keep their order; mapping keys are sorted.
    """
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
    """Return a transition's lineage reference: "s012l1:" and a hex SHA-256.

    The digest covers the lineage domain tag followed by the canonical bytes
    of the action, activity ID, sequence, actor identity ID and access epoch,
    payload fingerprint, occurrence time in canonical UTC form and lineage
    schema. The evidence entries are covered through the payload
    fingerprint; the authority references are not covered.
    """
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
    """Return a reference string with surrounding whitespace stripped.

    Raise ServiceActivityValidationError unless the value is a str whose
    stripped form has from 1 to maximum characters; name identifies the
    field in the message.
    """
    if not isinstance(value, str):
        raise ServiceActivityValidationError(f"{name} must be a string")
    stripped = value.strip()
    if not stripped or len(stripped) > maximum:
        raise ServiceActivityValidationError(f"{name} must be 1..{maximum} non-empty characters")
    return stripped


def _validate_occurred_at(value: datetime) -> datetime:
    """Return a timezone-aware datetime converted to UTC.

    Raise ServiceActivityValidationError for a value that is not a datetime
    or has no UTC offset. The time is not compared with any clock or with
    earlier transitions.
    """
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ServiceActivityValidationError("occurred_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _sorted_evidence_dicts(evidence_tuples: list[tuple[str, str]], actor_identity_id: UUID) -> list[dict]:
    """Build a command's evidence entries for fingerprinting, in canonical order.

    Each (kind, reference) pair becomes a mapping of evidence_kind, reference
    and supplied_by_identity_id, the supplier always being the actor. The
    entries are sorted by the UTF-8 bytes of the kind, then of the reference.
    """
    actor_str = str(actor_identity_id)
    entries = [
        {"evidence_kind": kind, "reference": ref, "supplied_by_identity_id": actor_str}
        for kind, ref in evidence_tuples
    ]
    entries.sort(key=lambda e: (e["evidence_kind"].encode("utf-8"), e["reference"].encode("utf-8")))
    return entries


def _evidence_result_from_rows(rows) -> EvidenceResult:
    """Return evidence rows as sorted (kind, reference) pairs for a result.

    Pairs are sorted by the UTF-8 bytes of the kind value, then of the
    reference. A stored kind outside the evidence kinds raises ValueError.
    """
    items = [(ServiceActivityEvidenceKind(r.evidence_kind), r.reference) for r in rows]
    items.sort(key=lambda t: (t[0].value.encode("utf-8"), t[1].encode("utf-8")))
    return tuple(items)


def _is_postgresql(alias: str) -> bool:
    """Report whether the database alias's ENGINE setting names postgresql."""
    return "postgresql" in connections[alias].settings_dict.get("ENGINE", "")


class ServiceActivityService:
    """Execute Service Activity commands against one database alias.

    Each public command method enters an atomic block, which may be nested
    within a caller's transaction. The injected authority qualifies whether
    the actor may act. This class checks actor eligibility, lifecycle state
    and replay conditions, and writes first-execution records or reconstructs
    a stored result.
    """
    def __init__(
        self,
        *,
        authority: ServiceCommandAuthority,
        clock: Callable[[], datetime],
        database_alias: str = "default",
    ) -> None:
        """Bind the service to an authority, a clock and a database alias.

        Refuses an authority that is not a ServiceCommandAuthority, an authority bound to a different alias, a
        clock that is not callable, and an empty alias. The clock supplies the time at which authority is
        evaluated, not the command's occurred_at.
        """
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
        """Lock and return the command actor's Identity, with its credential, for the current transaction.

        Refuses with ServiceActivityActorError if no Identity belongs to the credential, if the Identity is not
        ACTIVE, or if the credential is not active.
        """
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
        """Lock the other Identities a command touches, in ascending key order, and return them by key.

        The actor is excluded, since it is already locked. Rows are returned without an eligibility check;
        callers check only the identities that must be active.
        """
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
        """Refuse with ServiceActivityActorError unless the Identity and its credential are both active.

        The label names the role in the error message, for example "assignee".
        """
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
        """Build an authority request for this call and return its qualification result.

        Read the injected clock, validate its result as a timezone-aware datetime
        and normalise it to UTC. The request carries that evaluation time, the
        database alias, actor key, identity and current access epoch, action,
        target fingerprint, request reference and idempotency key.

        Clock, validation and authority exceptions propagate. The authority is
        called only after clock-result validation succeeds.
        """
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
        """Lock and return a stored transition for the actor, action and idempotency key.

        Return None when none is found. The expected_epoch and
        expected_fingerprint parameters are unused here. Callers separately
        compare those values through _resolve_replay and reconstruct the result.
        """
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
        """Compare a stored transition's access epoch and payload fingerprint.

        Raise ServiceActivityCrossEpochConflict for an epoch mismatch, then
        ServiceActivityPayloadConflict for a fingerprint mismatch. Return None
        when both match. Command-specific reconstruction performs further checks.
        """
        if existing.actor_access_epoch != expected_epoch:
            raise ServiceActivityCrossEpochConflict("cross-epoch conflict")
        if existing.payload_fingerprint != expected_fingerprint:
            raise ServiceActivityPayloadConflict("payload conflict")

    def _write_evidence(self, transition, actor, authority_reference: str, occurred_at: datetime, evidence_tuples: list[tuple[str, str]]):
        """Save evidence references on the service's database alias and return the rows.

        Create one row per (kind, reference) pair, bound to the supplied transition,
        actor, authority reference and occurrence time. Return the saved rows as
        a list. The caller supplies the enclosing transaction.
        """
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
        """Return stored evidence as a sorted tuple of (kind, reference) pairs.

        Raise ServiceActivityMalformedReplay if a row's occurrence time differs
        from the transition's, or its supplier differs from the transition's actor.
        Order pairs by the UTF-8 bytes of the kind value and reference; return an
        empty tuple when no rows exist. Command-specific reconstruction checks
        the allowed evidence kinds and counts.
        """
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
        """Run a write with named-constraint recovery on PostgreSQL.

        Call within an existing transaction on the service's alias. On PostgreSQL,
        run write_fn inside a nested atomic block. If an IntegrityError reports
        the constraint named by _IDEM_CONSTRAINT, roll back that block and call
        replay_fn within the enclosing transaction. Return the callback's result;
        other errors and failures of replay_fn propagate.

        On other databases, call write_fn directly without adding a savepoint
        or this recovery path.
        """
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
        """Create an Activity or replay its stored CREATE result.

        Enter an atomic block on the service's database alias and delegate to
        _execute_create.
        """
        with transaction.atomic(using=self._alias):
            return self._execute_create(command)

    def _execute_create(self, command: CreateServiceActivityCommand) -> CreateServiceActivityResult:
        """Execute CREATE within the caller's transaction, or reconstruct a matching replay.

        Validate and normalise references and occurrence time; validate the
        activity ID, ServiceVersion key and initiating domain. Find the version,
        raising ServiceActivityNotFound if absent, then lock its Service and the
        ServiceVersion, lock the actor, qualify authority, and compute the command
        payload fingerprint.

        If a transition already exists for this actor, CREATE and idempotency key,
        compare its epoch and fingerprint and reconstruct its result. Authority
        qualification therefore precedes replay lookup.

        For first execution, require a PUBLISHED Service and its current version,
        otherwise raising ServiceActivityLifecycleError. Write an UNASSIGNED
        Activity preserving the initiating domain and reference, its sequence-one
        transition with authority and lineage references, its head link, and one
        ACTIVITY_BASIS evidence row. Bind the Activity creation, transition and
        evidence occurrence to the normalised command occurred_at.

        Published/current-version checks apply only after no replay was found.
        Use _try_insert_with_savepoint for the writes: PostgreSQL recovery is
        limited to the named actor/action/idempotency constraint and still applies
        the replay comparisons and reconstruction checks. Other write failures
        propagate.
        """
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
        """Reconstruct a CREATE result from the supplied stored transition.

        Load its Activity and check the requested activity ID, matching Activity
        creation and transition occurrence times, sequence one, and no previous
        transition. Reconstruct evidence with its occurrence/supplier checks and
        require exactly one ACTIVITY_BASIS pair. Raise
        ServiceActivityMalformedReplay for these checked mismatches.

        Return the transition's recorded state, sequence, lineage reference and
        authority references with the reconstructed evidence. The recorded state
        is used even if the Activity has subsequently changed state.
        """
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
        """Assign an Activity or replay its stored ASSIGN result.

        Enter an atomic block on the service's database alias and delegate to
        _execute_assign.
        """
        with transaction.atomic(using=self._alias):
            return self._execute_assign(command)

    def _execute_assign(self, command: AssignServiceActivityCommand) -> AssignServiceActivityResult:
        """Execute ASSIGN within the caller's transaction, or reconstruct a matching replay.

        Validate and normalise the request reference, idempotency key, occurrence
        time, assignment reference and assignment basis reference, and require
        UUIDs for the activity and assignee identity IDs. Lock the Activity, then
        the actor; a missing Activity propagates ServiceActivity.DoesNotExist.
        Lock the assignee Identity with its credential, raising
        ServiceActivityActorError if it is absent or if it or its credential is not
        active. The actor may assign the Activity to itself.

        Qualify authority against a target naming the activity and the assignee,
        then compute the command payload fingerprint. If a transition already
        exists for this actor, ASSIGN and idempotency key, compare its epoch and
        fingerprint and reconstruct its result. The assignee's eligibility check
        and authority qualification therefore both precede replay lookup.

        For first execution, require the Activity to be UNASSIGNED, otherwise
        raising ServiceActivityLifecycleError, and lock its head transition. Write
        the UNASSIGNED to ASSIGNED transition with authority and lineage
        references; one assignment row recording the assignee, the actor as
        assigner and the assignment reference; the Activity's new state and head;
        and one ASSIGNMENT_BASIS evidence row supplied by the actor. Bind the
        transition, assignment and evidence occurrence to the normalised command
        occurred_at. The assignment reference is fingerprinted and stored on the
        assignment row; it is not an evidence row.

        An Activity has at most one assignment, and no command reassigns it. Use
        _try_insert_with_savepoint for the writes: PostgreSQL recovery is limited
        to the named actor/action/idempotency constraint and still applies the
        replay comparisons and reconstruction checks. Other write failures
        propagate.
        """
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
        """Reconstruct an ASSIGN result from the supplied stored transition.

        Load its Activity and check the requested activity ID. Require the
        assignment row written by that transition, with an assignment time equal
        to the transition's occurrence time. Reconstruct evidence with its
        occurrence/supplier checks and require exactly one ASSIGNMENT_BASIS pair.
        Raise ServiceActivityMalformedReplay for these checked mismatches.

        Return the transition's recorded state, sequence, lineage reference and
        authority references, the assignment ID and the reconstructed evidence.
        The recorded state is used even if the Activity has subsequently changed
        state.
        """
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
        """Accept an assignment or replay its stored ACCEPT_ASSIGNMENT result.

        Enter an atomic block on the service's database alias and delegate to
        _execute_accept.
        """
        with transaction.atomic(using=self._alias):
            return self._execute_accept(command)

    def _execute_accept(self, command: AcceptServiceAssignmentCommand) -> AcceptServiceAssignmentResult:
        """Execute ACCEPT within the caller's transaction, or reconstruct a matching replay.

        Validate and normalise the request reference, idempotency key and
        occurrence time, and require a UUID activity ID. Lock the Activity, then
        the actor; a missing Activity propagates ServiceActivity.DoesNotExist.
        Lock the Activity's assignment, raising ServiceActivityLifecycleError if
        none exists, and raise ServiceActivityActorError unless the actor is the
        assignee. These checks precede authority qualification.

        Qualify authority against a target naming the activity, then compute the
        command payload fingerprint. If a transition already exists for this
        actor, ACCEPT_ASSIGNMENT and idempotency key, compare its epoch and
        fingerprint and reconstruct its result.

        For first execution, require the Activity to be ASSIGNED, otherwise
        raising ServiceActivityLifecycleError, and lock its head transition. Write
        the ASSIGNED to IN_PROGRESS transition with authority and lineage
        references, bound to the normalised command occurred_at, and the
        Activity's new state and head. ACCEPT writes no evidence row and no child
        record. Use _try_insert_with_savepoint for the writes: PostgreSQL recovery
        is limited to the named actor/action/idempotency constraint and still
        applies the replay comparisons and reconstruction checks. Other write
        failures propagate.
        """
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
        """Reconstruct an ACCEPT result from the supplied stored transition.

        Load its Activity and check the requested activity ID, then require that
        the transition has no evidence rows. Raise ServiceActivityMalformedReplay
        for these checked mismatches.

        Return the transition's recorded state, sequence, lineage reference and
        authority references with empty evidence. The recorded state is used even
        if the Activity has subsequently changed state.
        """
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
        """Decline an assignment or replay its stored DECLINE_ASSIGNMENT result.

        Enter an atomic block on the service's database alias and delegate to
        _execute_decline.
        """
        with transaction.atomic(using=self._alias):
            return self._execute_decline(command)

    def _execute_decline(self, command: DeclineServiceAssignmentCommand) -> DeclineServiceAssignmentResult:
        """Execute DECLINE within the caller's transaction, or reconstruct a matching replay.

        Validate and normalise the request reference, idempotency key, occurrence
        time and decline basis reference, and require a UUID activity ID. Lock the
        Activity, then the actor; a missing Activity propagates
        ServiceActivity.DoesNotExist. Lock the Activity's assignment, raising
        ServiceActivityLifecycleError if none exists, and raise
        ServiceActivityActorError unless the actor is the assignee. These checks
        precede authority qualification.

        Qualify authority against a target naming the activity, then compute the
        command payload fingerprint. If a transition already exists for this
        actor, DECLINE_ASSIGNMENT and idempotency key, compare its epoch and
        fingerprint and reconstruct its result.

        For first execution, require the Activity to be ASSIGNED, otherwise
        raising ServiceActivityLifecycleError, and lock its head transition. Write
        the ASSIGNED to DECLINED transition with authority and lineage references,
        the Activity's new state and head, and one DECLINE_BASIS evidence row
        supplied by the actor, all bound to the normalised command occurred_at.
        DECLINED is terminal: no command moves a declined Activity, and it is not
        returned for reassignment. Use _try_insert_with_savepoint for the writes:
        PostgreSQL recovery is limited to the named actor/action/idempotency
        constraint and still applies the replay comparisons and reconstruction
        checks. Other write failures propagate.
        """
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
        """Reconstruct a DECLINE result from the supplied stored transition.

        Load its Activity and check the requested activity ID. Reconstruct
        evidence with its occurrence/supplier checks and require exactly one
        DECLINE_BASIS pair. Raise ServiceActivityMalformedReplay for these checked
        mismatches.

        Return the transition's recorded state, sequence, lineage reference,
        authority references and the reconstructed evidence.
        """
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
        """Submit work for an Activity or replay its stored SUBMIT_WORK result.

        Enter an atomic block on the service's database alias and delegate to
        _execute_submit.
        """
        with transaction.atomic(using=self._alias):
            return self._execute_submit(command)

    def _execute_submit(self, command: SubmitServiceWorkCommand) -> SubmitServiceWorkResult:
        """Execute SUBMIT within the caller's transaction, or reconstruct a matching replay.

        Validate and normalise the request reference, idempotency key,
        occurrence time and submission reference; require a UUID activity ID and
        a tuple of submission support references. Each support reference is
        validated and normalised, and a duplicate after normalisation raises
        ServiceActivityValidationError. The tuple may be empty.

        Lock the Activity, then the actor; a missing Activity propagates
        ServiceActivity.DoesNotExist. Lock the Activity's assignment, raising
        ServiceActivityLifecycleError if none exists, and raise
        ServiceActivityActorError unless the actor is the assignee. These checks
        precede authority qualification.

        Qualify authority against a target naming the activity, then compute the
        command payload fingerprint, including the submission reference and the
        normalised support references in their input order. If a transition
        already exists for this actor, SUBMIT_WORK and idempotency key, compare
        its epoch and fingerprint and reconstruct its result. With the other
        replay inputs unchanged, reordering distinct support references changes
        the fingerprint and raises ServiceActivityPayloadConflict. Returned
        evidence pairs are sorted by the UTF-8 bytes of their kind and
        reference; that ordering does not make the command support-reference
        tuple unordered.

        For first execution, require the Activity to be IN_PROGRESS, otherwise
        raising ServiceActivityLifecycleError, and lock its head transition.
        Write the IN_PROGRESS to SUBMITTED transition with authority and lineage
        references; one work submission recording the actor as submitter and the
        submission reference; the Activity's new state and head; and one
        SUBMISSION_SUPPORT evidence row per support reference, supplied by the
        actor. Bind the transition, submission and evidence occurrence to the
        normalised command occurred_at. The submission stores references, not
        work content. An Activity has at most one submission, and no command
        resubmits it.

        Use _try_insert_with_savepoint for the writes: PostgreSQL recovery is
        limited to the named actor/action/idempotency constraint and still
        applies the replay comparisons and reconstruction checks. Other write
        failures propagate.
        """
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
        """Reconstruct a SUBMIT result from the supplied stored transition.

        Load its Activity and check the requested activity ID. Require the work
        submission row written by that transition, with a submission time equal
        to the transition's occurrence time. Reconstruct evidence with its
        occurrence/supplier checks and require every pair to be
        SUBMISSION_SUPPORT; any number, including none, is accepted. Raise
        ServiceActivityMalformedReplay for these checked mismatches.

        Return the transition's recorded state, sequence, lineage reference and
        authority references, the work submission ID and the reconstructed
        evidence. The recorded state is used even if the Activity has
        subsequently changed state.
        """
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
        """Review submitted work or replay its stored REVIEW_WORK result.

        Enter an atomic block on the service's database alias and delegate to
        _execute_review.
        """
        with transaction.atomic(using=self._alias):
            return self._execute_review(command)

    def _execute_review(self, command: ReviewServiceWorkCommand) -> ReviewServiceWorkResult:
        """Execute REVIEW within the caller's transaction, or reconstruct a matching replay.

        Validate and normalise the request reference, idempotency key,
        occurrence time, review reference and review record reference, and
        require a UUID activity ID. Lock the Activity, then the actor with its
        credential. A missing Activity propagates ServiceActivity.DoesNotExist;
        a missing actor Identity, an inactive Identity or an inactive credential
        raises ServiceActivityActorError. Lock the Activity's assignment,
        raising ServiceActivityLifecycleError if none exists, then lock the
        assignee and the Activity's creator, other than the actor, in ascending
        key order; their current eligibility is not checked.

        Beyond actor eligibility, this method imposes no reviewer-role or
        separation-of-duties restriction: an eligible assignee or creator may
        review if the injected authority qualifies that actor. Qualify authority
        against a target naming the activity, then compute the command payload
        fingerprint. If a transition already exists for this actor, REVIEW_WORK
        and idempotency key, compare its epoch and fingerprint and reconstruct
        its result.

        For first execution, require the Activity to be SUBMITTED, otherwise
        raising ServiceActivityLifecycleError, then lock its head transition and
        its work submission; a missing submission also raises
        ServiceActivityLifecycleError. Write the SUBMITTED to REVIEWED
        transition with authority and lineage references; one review of that
        submission recording the actor as reviewer and the review reference; the
        Activity's new state and head; and one REVIEW_RECORD evidence row
        supplied by the actor, all bound to the normalised command occurred_at.
        The review records that a review occurred, with its references; it
        carries no verdict. A submission has at most one review.

        Use _try_insert_with_savepoint for the writes: PostgreSQL recovery is
        limited to the named actor/action/idempotency constraint and still
        applies the replay comparisons and reconstruction checks. Other write
        failures propagate.
        """
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
        """Reconstruct a REVIEW result from the supplied stored transition.

        Load its Activity and check the requested activity ID. Require the review
        row written by that transition, with a review time equal to the
        transition's occurrence time. Reconstruct evidence with its
        occurrence/supplier checks and require exactly one REVIEW_RECORD pair.
        Raise ServiceActivityMalformedReplay for these checked mismatches.

        Return the transition's recorded state, sequence, lineage reference and
        authority references, the review ID and the reconstructed evidence. The
        recorded state is used even if the Activity has subsequently changed
        state.
        """
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
        """Complete a reviewed Activity or replay its stored COMPLETE_ACTIVITY result.

        Enter an atomic block on the service's database alias and delegate to
        _execute_complete.
        """
        with transaction.atomic(using=self._alias):
            return self._execute_complete(command)

    def _execute_complete(self, command: CompleteServiceActivityCommand) -> CompleteServiceActivityResult:
        """Execute COMPLETE within the caller's transaction, or reconstruct a matching replay.

        Validate and normalise the request reference, idempotency key,
        occurrence time and completion record reference, and require a UUID
        activity ID. Lock the Activity, then the actor with its credential. A
        missing Activity propagates ServiceActivity.DoesNotExist; a missing
        actor Identity, an inactive Identity or an inactive credential raises
        ServiceActivityActorError. Lock the Activity's assignment, its work
        submission and that submission's review, raising
        ServiceActivityLifecycleError if any is missing, then lock the assignee,
        the creator and the reviewer, other than the actor, in ascending key
        order; their current eligibility is not checked. These lookups precede
        authority qualification, replay lookup and the state check. The first
        missing required record raises the lifecycle error; when the assignment
        and submission exist but the review does not, the missing review is
        refused before the Activity's state is checked.

        Beyond actor eligibility, this method imposes no completer-role or
        separation-of-duties restriction; the injected authority decides whether
        the eligible actor may complete. Qualify authority against a target
        naming the activity, then compute the command payload fingerprint. If a
        transition already exists for this actor, COMPLETE_ACTIVITY and
        idempotency key, compare its epoch and fingerprint and reconstruct its
        result.

        For first execution, require the Activity to be REVIEWED, otherwise
        raising ServiceActivityLifecycleError, and lock its head transition.
        Write the REVIEWED to COMPLETED transition with authority and lineage
        references, the Activity's new state and head, and one COMPLETION_RECORD
        evidence row supplied by the actor, all bound to the normalised command
        occurred_at. COMPLETE writes no child record. COMPLETED is terminal;
        completion records the lifecycle occurrence only, not that the work was
        accepted, approved or of any quality.

        Use _try_insert_with_savepoint for the writes: PostgreSQL recovery is
        limited to the named actor/action/idempotency constraint and still
        applies the replay comparisons and reconstruction checks. Other write
        failures propagate.
        """
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
        """Reconstruct a COMPLETE result from the supplied stored transition.

        Load its Activity and check the requested activity ID, then reconstruct
        evidence with its occurrence/supplier checks and require exactly one
        COMPLETION_RECORD pair. Raise ServiceActivityMalformedReplay for these
        checked mismatches.

        Return the transition's recorded state, sequence, lineage reference,
        authority references and the reconstructed evidence.
        """
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
        """Cancel an Activity or replay its stored CANCEL_ACTIVITY result.

        Enter an atomic block on the service's database alias and delegate to
        _execute_cancel.
        """
        with transaction.atomic(using=self._alias):
            return self._execute_cancel(command)

    def _execute_cancel(self, command: CancelServiceActivityCommand) -> CancelServiceActivityResult:
        """Execute CANCEL within the caller's transaction, or reconstruct a matching replay.

        Validate and normalise the request reference, idempotency key,
        occurrence time and cancellation basis reference, and require a UUID
        activity ID. Lock the Activity, then the actor with its credential; a
        missing Activity propagates ServiceActivity.DoesNotExist. Raise
        ServiceActivityActorError if the actor Identity is absent or if the
        Identity or its credential is not active.

        Read the Activity's assignment and work submission, and the submission's
        review where present, without locking those rows. Then lock the creator
        and any assignee and reviewer, other than the actor, in ascending key
        order; their current eligibility is not checked.

        Beyond actor eligibility, this method imposes no role restriction on who
        may cancel; the injected authority decides. Qualify authority against a
        target naming the activity, then compute the command payload fingerprint.
        If a transition already exists for this actor, CANCEL_ACTIVITY and
        idempotency key, compare its epoch and fingerprint and reconstruct its
        result.

        For first execution, require a non-terminal state (UNASSIGNED, ASSIGNED,
        IN_PROGRESS, SUBMITTED or REVIEWED), otherwise raising
        ServiceActivityLifecycleError, and lock the head transition. Write a
        transition from the current state to CANCELLED with authority and
        lineage references, the Activity's new state and head, and one
        CANCELLATION_BASIS evidence row supplied by the actor. Bind the transition
        and evidence occurrence to the normalised command occurred_at. CANCEL
        writes no separate child record, and CANCELLED is terminal.

        Use _try_insert_with_savepoint for the writes: PostgreSQL recovery is
        limited to the named actor/action/idempotency constraint and still
        applies the replay comparisons and reconstruction checks. Other write
        failures propagate.
        """
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
        """Reconstruct a CANCEL result from the supplied stored transition.

        Load its Activity and check the requested activity ID, then reconstruct
        evidence with its occurrence/supplier checks and require exactly one
        CANCELLATION_BASIS pair. Raise ServiceActivityMalformedReplay for these
        checked mismatches.

        Return the transition's recorded state, sequence, lineage reference,
        authority references and the reconstructed evidence.
        """
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
        """Lock and return the Activity's head transition, or None if it has none.

        The head is taken from the Activity row as already loaded and locked.
        """
        if activity.head_transition_id is None:
            return None
        return (
            ServiceActivityTransition.objects.using(self._alias)
            .select_for_update()
            .get(pk=activity.head_transition_id)
        )

    def _get_assignment(self, activity: ServiceActivity) -> ServiceActivityAssignment:
        """Lock and return the Activity's assignment.

        Raise ServiceActivityLifecycleError if no assignment exists.
        """
        try:
            return (
                ServiceActivityAssignment.objects.using(self._alias)
                .select_for_update()
                .get(activity=activity)
            )
        except ServiceActivityAssignment.DoesNotExist:
            raise ServiceActivityLifecycleError("no assignment exists for this Activity")

    def _get_submission(self, activity: ServiceActivity) -> ServiceWorkSubmission:
        """Lock and return the Activity's work submission.

        Raise ServiceActivityLifecycleError if no work submission exists.
        """
        try:
            return (
                ServiceWorkSubmission.objects.using(self._alias)
                .select_for_update()
                .get(activity=activity)
            )
        except ServiceWorkSubmission.DoesNotExist:
            raise ServiceActivityLifecycleError("no work submission exists for this Activity")

    def _get_review(self, submission: ServiceWorkSubmission) -> ServiceActivityReview:
        """Lock and return the review of a work submission.

        Raise ServiceActivityLifecycleError if no review exists.
        """
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
