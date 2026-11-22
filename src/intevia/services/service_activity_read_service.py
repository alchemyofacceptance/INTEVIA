"""SERVICE-owned read service for S012 Activity readback."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol
from uuid import UUID

from django.contrib.auth.models import User
from django.db import connections, transaction

from src.intevia.services.committees import work_holder

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
    ServiceCommandAuthorityResponse,
    canonical_timestamp,
    decision_reference_for,
)

ServiceCommandAction = ServiceActivityTransition.Action
ServiceActivityState = ServiceActivity.State
ServiceActivityEvidenceKind = ServiceActivityEvidenceReference.Kind
InitiatingDomain = ServiceActivity.InitiatingDomain

_COMMAND_DOMAIN = b"INTEVIA:S012:COMMAND:v1\x00"
_COMMAND_SCHEMA = "intevia.s012.command.v1"
_TARGET_DOMAIN = b"INTEVIA:S012:AUTHORITY_TARGET:v1\x00"
_TARGET_SCHEMA = "intevia.s012.authority-target.v1"
_LINEAGE_DOMAIN = b"INTEVIA:S012:TRANSITION_LINEAGE:v1\x00"
_LINEAGE_SCHEMA = "intevia.s012.lineage.v1"
_SUBMISSION_QUALIFICATION_DOMAIN = (
    b"INTEVIA:S012:SERVICE_SUBMISSION_QUALIFICATION:v1\x00"
)
_SUBMISSION_QUALIFICATION_SCHEMA = (
    "intevia.s012.service-submission-qualification.v1"
)

TERMINAL_STATES = frozenset({
    ServiceActivityState.COMPLETED,
    ServiceActivityState.DECLINED,
    ServiceActivityState.CANCELLED,
})

_VALID_EDGES: frozenset[tuple[str, str | None, str]] = frozenset({
    ("CREATE", None, "unassigned"),
    ("ASSIGN", "unassigned", "assigned"),
    ("ACCEPT_ASSIGNMENT", "assigned", "in_progress"),
    ("DECLINE_ASSIGNMENT", "assigned", "declined"),
    ("SUBMIT_WORK", "in_progress", "submitted"),
    ("REVIEW_WORK", "submitted", "reviewed"),
    ("COMPLETE_ACTIVITY", "reviewed", "completed"),
    ("CANCEL_ACTIVITY", "unassigned", "cancelled"),
    ("CANCEL_ACTIVITY", "assigned", "cancelled"),
    ("CANCEL_ACTIVITY", "in_progress", "cancelled"),
    ("CANCEL_ACTIVITY", "submitted", "cancelled"),
    ("CANCEL_ACTIVITY", "reviewed", "cancelled"),
})

_STATE_MESSAGES: dict[str, str] = {
    "unassigned": (
        "This Service Activity is recorded as unassigned. No judgement about the"
        " work, its priority, or any Human is recorded."
    ),
    "assigned": (
        "This Service Activity is recorded as assigned for orchestration purposes."
        " Assignment does not record competence, acceptance, contribution, or value."
    ),
    "in_progress": (
        "This Service Activity is recorded as in progress for orchestration purposes."
        " This state does not assess pace, quality, competence, correctness, or value."
    ),
    "submitted": (
        "A Work Submission reference is recorded for this Service Activity."
        " Submission does not record acceptance, correctness, competence,"
        " contribution, recognition, or value."
    ),
    "reviewed": (
        "A review occurrence is recorded for this Service Activity. Review does not"
        " record acceptance, correctness, approval, competence, contribution,"
        " recognition, or value."
    ),
    "completed": (
        "This Service Activity is recorded as complete for orchestration purposes."
        " Completion does not record whether the work was accepted, correct, competent,"
        " recognised, valuable, or a contribution."
    ),
    "cancelled": (
        "This Service Activity is recorded as cancelled. Earlier assignment,"
        " submission, review, and evidence records remain part of its history."
        " Cancellation does not determine whether earlier work was correct, accepted,"
        " competent, recognised, valuable, or a contribution."
    ),
    "declined": (
        "This assignment was declined and this Service Activity is terminal in S012."
        " Decline does not imply fault, inability, or judgement about the assignee."
    ),
}


class ServiceActivityReadError(Exception):
    """Base error for Activity reads and qualifications.

    Raised for an activity_id that is not a UUID, for a
    qualification outside an atomic block, and for every
    refusal in a stored qualification payload: its fields,
    the exact types, non-NFC text, a naive datetime and the
    object keys.
    """
    pass


class ServiceActivityReadNotFound(ServiceActivityReadError):
    """No Activity carries the requested activity_id.

    Raised by both commands after the Activity lookup
    (l.485, l.607), before any visibility or lineage work.
    """
    pass


class ServiceActivityReadNotAuthorised(ServiceActivityReadError):
    """The viewer may not read this Activity.

    Raised when the credential has no Identity, the Identity
    is inactive, or its credential is inactive. On the
    provider path the clock must return a datetime whose
    tzinfo is present; the offset itself is not checked. The
    answer must be a visibility response whose five echoed
    fields match the request, whose authority reference is
    text of 1 to 255 characters after stripping, and whose
    visible is True itself.
    """
    pass


class ServiceActivityReadLineageError(ServiceActivityReadError):
    """Stored Activity records fail a lineage check.

    Raised through the chain, state, child-row and evidence
    checks, and again where a command, fingerprint or
    reference is reconstructed. Qualification adds its own:
    exactly one valid SUBMIT_WORK with its assignment and
    submission, and the actor, submitter and work holder
    agreeing at the submission's own time.
    """
    pass


@dataclass(frozen=True, slots=True)
class ServiceActivityVisibilityRequest:
    """What a third-party visibility provider is asked.

    Built by _check_visibility (l.707-713) once the viewer is
    neither the creator nor the work holder: the alias, the
    viewer's Identity and access epoch, the Activity, and the
    clock's instant converted to UTC. Frozen; no secret and
    no row is carried.
    """
    database_alias: str
    viewer_identity_id: UUID
    viewer_access_epoch: int
    activity_id: UUID
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class ServiceActivityVisibilityResponse:
    """The provider's answer to a visibility request.

    The reader checks the type, then the five echoed fields
    against the request, then that the authority reference is
    text of 1 to 255 characters after stripping, then that
    visible is True itself. The reference is neither looked
    up nor kept: it is checked and discarded.
    """
    database_alias: str
    viewer_identity_id: UUID
    viewer_access_epoch: int
    activity_id: UUID
    evaluated_at: datetime
    visible: bool
    authority_reference: str


class ServiceActivityVisibilityProvider(Protocol):
    """The third-party visibility decision the reader needs.

    A structural protocol. Construction checks only that the
    object supplied has a callable check_visibility, so a
    callable of any signature passes. The reader calls it
    with request as a keyword.
    """
    def check_visibility(
        self,
        *,
        request: ServiceActivityVisibilityRequest,
    ) -> ServiceActivityVisibilityResponse | None:
        """Decide whether this viewer may read this Activity.

        Takes the request by keyword. Returns a response, or None
        for no decision; the reader treats None and any other
        type alike, as a refusal.
        """
        ...


@dataclass(frozen=True, slots=True)
class ServiceActivityEvidenceDTO:
    """One evidence reference carried on a history entry.

    Its kind and reference, as stored.
    """
    evidence_kind: ServiceActivityEvidenceKind
    reference: str


@dataclass(frozen=True, slots=True)
class ServiceActivityHistoryEntryDTO:
    """One transition in the Activity's validated history.

    The sequence, action, the states it moved between, the
    actor, when it occurred, and its stored lineage
    reference. The builder sorts the evidence by the UTF-8
    bytes of the kind and then the reference. Only entries
    whose lineage was accepted reach a caller.
    """
    sequence: int
    action: ServiceCommandAction
    from_state: ServiceActivityState | None
    to_state: ServiceActivityState
    actor_identity_id: UUID
    occurred_at: datetime
    lineage_reference: str
    evidence: tuple[ServiceActivityEvidenceDTO, ...]


@dataclass(frozen=True, slots=True)
class ServiceActivityAssignmentDTO:
    """The Activity's current assignment, as returned.

    The assignee, who assigned them, the assignment reference
    and when it was made. Assignment records orchestration
    only: no competence, acceptance or contribution.
    """
    assignee_identity_id: UUID
    assigned_by_identity_id: UUID
    assignment_reference: str
    assigned_at: datetime


@dataclass(frozen=True, slots=True)
class ServiceWorkSubmissionDTO:
    """The Activity's Work Submission, as returned.

    Who submitted, the submission reference and when.
    Submission records neither acceptance nor correctness.
    """
    submitted_by_identity_id: UUID
    submission_reference: str
    submitted_at: datetime


@dataclass(frozen=True, slots=True)
class ServiceActivityReviewDTO:
    """The Activity's review occurrence, as returned.

    Who reviewed, the review reference and when. A review
    records that it happened, not its outcome.
    """
    reviewed_by_identity_id: UUID
    review_reference: str
    reviewed_at: datetime


@dataclass(frozen=True, slots=True)
class ServiceActivityReadDTO:
    """Everything read_service_activity returns.

    The Activity, its Service and Service version, the
    Service's own state and whether that version is current,
    the initiating domain and reference, the Activity's state
    with the fixed message for it (_STATE_MESSAGES, l.74-112),
    the assignment, submission and review where they exist,
    and the ordered history.
    """
    activity_id: UUID
    service_id: int
    service_version_id: int
    service_state: str
    service_version_is_current: bool
    initiating_domain: InitiatingDomain
    initiating_domain_reference: str
    state: ServiceActivityState
    state_message: str
    assignment: ServiceActivityAssignmentDTO | None
    work_submission: ServiceWorkSubmissionDTO | None
    review: ServiceActivityReviewDTO | None
    history: tuple[ServiceActivityHistoryEntryDTO, ...]


@dataclass(frozen=True, slots=True)
class ServiceSubmissionQualificationDTO:
    """The qualification of one validated SUBMIT_WORK.

    Returned by qualify_submission_occurrence: the alias, the
    Activity by primary key and id, the transition by primary
    key, sequence and lineage reference, the subject and
    actor, when it occurred, the actor's access epoch, the
    authority reference it came from, and the qualification's
    own schema, contract version and reference.
    actor_equals_assignee is always True: the service checks
    the actor, submitter and work holder first, and refuses
    otherwise.
    """
    database_alias: str
    activity_pk: int
    activity_id: UUID
    submit_transition_pk: int
    submit_transition_sequence: int
    submit_transition_lineage_reference: str
    subject_pk: int
    subject_identity_id: UUID
    actor_identity_id: UUID
    actor_equals_assignee: bool
    occurred_at: datetime
    actor_access_epoch: int
    source_authority_reference: str
    qualification_schema: str
    qualification_contract_version: int
    qualification_reference: str


def _canonical_bytes(obj: object) -> bytes:
    """Serialise a value for fingerprinting.

    Compact UTF-8 JSON with sorted keys. Text is escaped by
    JSON's own rules and non-ASCII characters are emitted
    directly. NFC is not checked and finite floats are
    accepted. NaN and the infinities raise ValueError; a UUID
    or datetime raises TypeError.
    """
    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


_SUBMISSION_QUALIFICATION_FIELDS = frozenset({
    "actor_access_epoch",
    "actor_equals_assignee",
    "actor_identity_id",
    "activity_id",
    "contract_version",
    "database_alias",
    "occurred_at",
    "schema",
    "source_authority_reference",
    "subject_identity_id",
    "submit_transition_lineage_reference",
    "submit_transition_pk",
    "submit_transition_sequence",
})
_SUBMISSION_QUALIFICATION_INTEGER_FIELDS = frozenset({
    "actor_access_epoch",
    "contract_version",
    "submit_transition_pk",
    "submit_transition_sequence",
})


def _qualification_canonical_bytes(payload: object) -> bytes:
    """Serialise a stored qualification payload, strictly.

    The payload must be an exact dict holding exactly the
    thirteen qualification fields. actor_access_epoch,
    contract_version, submit_transition_pk and
    submit_transition_sequence must each be an exact int, and
    actor_equals_assignee an exact bool. The other eight
    fields carry no type or meaning check of their own. The
    whole object is then canonicalised and returned as
    compact, key-sorted UTF-8 JSON bytes. Shape, type and
    canonicalisation failures raise ServiceActivityReadError;
    timestamp, JSON and encoding errors propagate.
    """
    if type(payload) is not dict or set(payload) != _SUBMISSION_QUALIFICATION_FIELDS:
        raise ServiceActivityReadError("qualification payload fields are invalid")
    for field in _SUBMISSION_QUALIFICATION_INTEGER_FIELDS:
        if type(payload[field]) is not int:
            raise ServiceActivityReadError(f"qualification {field} must be an exact integer")
    if type(payload["actor_equals_assignee"]) is not bool:
        raise ServiceActivityReadError(
            "qualification actor_equals_assignee must be an exact Boolean"
        )

    def canonicalise(value: object) -> object:
        """Reduce one payload value to its canonical form.

        None and exact bools and ints are kept. An exact str is
        kept only if it is already NFC. A UUID becomes its text,
        and a datetime carrying an offset becomes UTC text with
        microseconds and a Z; both are matched by isinstance, so
        subclasses pass. Exact lists and tuples become lists. An
        exact dict is rebuilt key by key, each key an exact str
        in NFC and not already present. Anything else, floats and
        bytes among them, raises ServiceActivityReadError. A
        naive datetime is refused here, before the shared
        timestamp helper, whose own errors propagate.
        """
        if value is None or type(value) in {bool, int}:
            return value
        if type(value) is str:
            if unicodedata.normalize("NFC", value) != value:
                raise ServiceActivityReadError("qualification text must be NFC")
            return value
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ServiceActivityReadError("qualification datetime must be aware")
            return canonical_timestamp(value)
        if type(value) in {list, tuple}:
            return [canonicalise(item) for item in value]
        if type(value) is dict:
            result = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise ServiceActivityReadError(
                        "qualification object keys must be strings"
                    )
                if unicodedata.normalize("NFC", key) != key:
                    raise ServiceActivityReadError(
                        "qualification object keys must be NFC"
                    )
                if key in result:
                    raise ServiceActivityReadError(
                        "qualification object fields must be unique"
                    )
                result[key] = canonicalise(item)
            return result
        raise ServiceActivityReadError("qualification value type is unsupported")

    return json.dumps(
        canonicalise(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _recompute_target_fingerprint(
    action: ServiceCommandAction,
    activity_id: UUID,
    **extras: object,
) -> str:
    """Recompute an authority target's fingerprint.

    The action's value, the Activity as text and the target
    schema, with extras merged over them, serialised
    canonically under the target domain and hashed with
    SHA-256; the hex digest alone is returned. Extras may
    replace the schema; action and activity_id are
    parameters, so an extra of either name fails at binding
    with TypeError. Extras are not checked for shape or type:
    a UUID raises TypeError from the serialiser, whose
    ValueError and TypeError otherwise propagate.
    """
    target = {
        "action": action.value,
        "activity_id": str(activity_id),
        "schema": _TARGET_SCHEMA,
    }
    target.update(extras)
    digest = hashlib.sha256(_TARGET_DOMAIN + _canonical_bytes(target)).hexdigest()
    return digest


def _recompute_payload_fingerprint(
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
    """Recompute a command payload's fingerprint.

    The action, actor and access epoch, the request reference
    and idempotency key, the occurrence as a canonical UTC
    timestamp, and the target, command and evidence as given,
    hashed with SHA-256 under the command domain. A naive
    occurred_at raises ValueError from canonical_timestamp.
    The target, command and evidence are taken as built: this
    does not check their shape.
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


def _recompute_lineage_reference(
    activity_id: UUID,
    sequence: int,
    action: ServiceCommandAction,
    actor_identity_id: UUID,
    actor_access_epoch: int,
    payload_fingerprint: str,
    occurred_at: datetime,
) -> str:
    """Recompute a transition's lineage reference.

    The Activity, sequence, action, actor, access epoch,
    payload fingerprint and canonical occurrence, hashed with
    SHA-256 under the lineage domain and returned with the
    s012l1 prefix. A naive occurred_at raises ValueError from
    canonical_timestamp.
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


def _recompute_decision_reference(
    database_alias: str,
    actor_pk: int,
    actor_identity_id: UUID,
    actor_access_epoch: int,
    action: ServiceCommandAction,
    target_fingerprint: str,
    request_reference: str,
    idempotency_key: str,
    evaluated_at: datetime,
    authority_reference: str,
) -> str:
    """Recompute an authority decision's reference.

    The stored fields are rebuilt into an authority response
    and passed to decision_reference_for, which returns the
    s012d1 reference. The authority module's own canonical
    rules apply there, not this module's: a naive
    evaluated_at raises ValueError, and so does a top-level
    field name that is not NFC text, a non-NFC string value
    or a float value. No provider is called and no decision
    is made.
    """
    response = ServiceCommandAuthorityResponse(
        database_alias=database_alias,
        actor_pk=actor_pk,
        actor_identity_id=actor_identity_id,
        actor_access_epoch=actor_access_epoch,
        action=action,
        target_fingerprint=target_fingerprint,
        request_reference=request_reference,
        idempotency_key=idempotency_key,
        evaluated_at=evaluated_at,
        authority_reference=authority_reference,
    )
    return decision_reference_for(response)


def _sorted_evidence_dicts(
    evidence_rows: list[ServiceActivityEvidenceReference],
    actor_identity_id: UUID,
) -> list[dict]:
    """Rebuild a transition's evidence for fingerprinting.

    The actor's id is taken as text once, and each row
    contributes its kind and reference with that text as the
    supplier; the row's own supplier is neither read nor
    checked here. The list is sorted by the UTF-8 bytes of
    the kind and then the reference, which for text orders by
    code point, and equal entries keep their input order. An
    empty list gives an empty list. A kind or reference that
    is not text raises AttributeError from the sort.
    """
    actor_str = str(actor_identity_id)
    entries = [
        {
            "evidence_kind": row.evidence_kind,
            "reference": row.reference,
            "supplied_by_identity_id": actor_str,
        }
        for row in evidence_rows
    ]
    entries.sort(
        key=lambda e: (
            e["evidence_kind"].encode("utf-8"),
            e["reference"].encode("utf-8"),
        )
    )
    return entries


class ServiceActivityReadService:
    """Read an S012 Activity, or qualify its work submission.

    The viewer's read checks visibility and returns the
    Activity with its history. Qualification runs in the
    caller's atomic block and takes no viewer. Both ask for
    row locks and check the stored lineage, and neither
    writes an Activity record. Only the viewer's path uses
    the injected provider and clock, and only when a
    third-party decision is needed.
    """
    def __init__(
        self,
        *,
        visibility_provider: ServiceActivityVisibilityProvider,
        clock: Callable[[], datetime],
        database_alias: str = "default",
    ) -> None:
        """Hold the provider, the clock and the alias.

        The provider must carry a callable check_visibility, and
        the clock must be callable, or TypeError is raised. The
        alias must be a non-empty str, or ValueError. The
        provider's signature is not checked, the clock is not
        called, and the alias is not resolved: a name with no
        database configured is accepted here and fails at the
        first read.
        """
        if visibility_provider is None or not callable(
            getattr(visibility_provider, "check_visibility", None)
        ):
            raise TypeError("visibility_provider must implement check_visibility")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not isinstance(database_alias, str) or not database_alias:
            raise ValueError("database_alias is required")
        self._visibility_provider = visibility_provider
        self._clock = clock
        self._alias = database_alias

    def read_service_activity(
        self,
        *,
        credential: User,
        activity_id: UUID,
    ) -> ServiceActivityReadDTO:
        """Return the viewer's Activity read, with its history.

        A non-UUID activity_id raises ServiceActivityReadError
        before the credential is looked at. The locked read then
        runs in an atomic block on the configured alias, so a
        caller needs no transaction of its own; where one is
        already open on that alias, this read nests inside it.
        """
        if not isinstance(activity_id, UUID):
            raise ServiceActivityReadError("activity_id must be a UUID")
        with transaction.atomic(using=self._alias):
            return self._execute_read(credential, activity_id)

    def qualify_submission_occurrence(
        self,
        *,
        activity_id: UUID,
    ) -> ServiceSubmissionQualificationDTO:
        """Qualify one SUBMIT_WORK in the caller's atomic block.

        A UUID is required, and then an active Django atomic
        block on this alias, or ServiceActivityReadError is
        raised. The Activity is asked for with a lock; absence
        raises ServiceActivityReadNotFound. Its named head is
        fetched if it has one, then its transitions by sequence
        and primary key, the first assignment, the first
        submission and, where there is one, that submission's
        first review, then all evidence by transition, kind and
        reference. Each of those queries asks for row locks, in
        that order.

        The lineage is validated. Exactly one SUBMIT_WORK, with
        an assignment and a submission, is then required, and
        its actor must equal the submitter, who must be a work
        holder at the submission's own time; either failure
        raises ServiceActivityReadLineageError. No viewer is
        involved and no visibility decision is made.

        The canonical thirteen-field payload is hashed under the
        qualification domain, and the occurrence is returned
        with actor_equals_assignee true and an s012sq1
        reference. An invalid payload raises
        ServiceActivityReadError; other lookup, database,
        work_holder and conversion errors propagate.
        """
        if not isinstance(activity_id, UUID):
            raise ServiceActivityReadError("activity_id must be a UUID")
        if not connections[self._alias].in_atomic_block:
            raise ServiceActivityReadError("an active outer transaction is required")

        try:
            activity = (
                ServiceActivity.objects.using(self._alias)
                .select_for_update()
                .get(activity_id=activity_id)
            )
        except ServiceActivity.DoesNotExist:
            raise ServiceActivityReadNotFound("ServiceActivity not found")

        head = None
        if activity.head_transition_id is not None:
            head = (
                ServiceActivityTransition.objects.using(self._alias)
                .select_for_update()
                .get(pk=activity.head_transition_id)
            )
        transitions = list(
            ServiceActivityTransition.objects.using(self._alias)
            .select_for_update()
            .select_related("actor")
            .filter(activity=activity)
            .order_by("sequence", "pk")
        )
        assignment = (
            ServiceActivityAssignment.objects.using(self._alias)
            .select_for_update()
            .select_related("assignee", "assigned_by")
            .filter(activity=activity)
            .first()
        )
        submission = (
            ServiceWorkSubmission.objects.using(self._alias)
            .select_for_update()
            .select_related("submitted_by")
            .filter(activity=activity)
            .first()
        )
        review = None
        if submission is not None:
            review = (
                ServiceActivityReview.objects.using(self._alias)
                .select_for_update()
                .select_related("reviewed_by")
                .filter(submission=submission)
                .first()
            )
        all_evidence = list(
            ServiceActivityEvidenceReference.objects.using(self._alias)
            .select_for_update()
            .select_related("supplied_by")
            .filter(transition__activity=activity)
            .order_by("transition_id", "evidence_kind", "reference")
        )

        self._validate_lineage(
            activity, transitions, head, assignment, submission, review, all_evidence
        )
        submit_transitions = [
            transition_row
            for transition_row in transitions
            if transition_row.action == ServiceCommandAction.SUBMIT_WORK.value
        ]
        if len(submit_transitions) != 1 or assignment is None or submission is None:
            raise ServiceActivityReadLineageError(
                "exactly one valid SUBMIT_WORK occurrence is required"
            )
        submit_transition = submit_transitions[0]
        if not (
            submit_transition.actor_id
            == submission.submitted_by_id
            == work_holder(activity, assignment, self._alias, submission.submitted_at, person=submission.submitted_by_id)
        ):
            raise ServiceActivityReadLineageError(
                "submit actor, submitter, and immutable assignee must match"
            )

        qualification_payload = {
            "actor_access_epoch": submit_transition.actor_access_epoch,
            "actor_equals_assignee": True,
            "actor_identity_id": submit_transition.actor.identity_id,
            "activity_id": activity.activity_id,
            "contract_version": 1,
            "database_alias": self._alias,
            "occurred_at": submit_transition.occurred_at,
            "schema": _SUBMISSION_QUALIFICATION_SCHEMA,
            "source_authority_reference": submit_transition.authority_reference,
            "subject_identity_id": submission.submitted_by.identity_id,
            "submit_transition_lineage_reference": (
                submit_transition.lineage_reference
            ),
            "submit_transition_pk": submit_transition.pk,
            "submit_transition_sequence": submit_transition.sequence,
        }
        qualification_digest = hashlib.sha256(
            _SUBMISSION_QUALIFICATION_DOMAIN
            + _qualification_canonical_bytes(qualification_payload)
        ).hexdigest()
        return ServiceSubmissionQualificationDTO(
            database_alias=self._alias,
            activity_pk=activity.pk,
            activity_id=activity.activity_id,
            submit_transition_pk=submit_transition.pk,
            submit_transition_sequence=submit_transition.sequence,
            submit_transition_lineage_reference=submit_transition.lineage_reference,
            subject_pk=submission.submitted_by_id,
            subject_identity_id=submission.submitted_by.identity_id,
            actor_identity_id=submit_transition.actor.identity_id,
            actor_equals_assignee=True,
            occurred_at=submit_transition.occurred_at,
            actor_access_epoch=submit_transition.actor_access_epoch,
            source_authority_reference=submit_transition.authority_reference,
            qualification_schema=_SUBMISSION_QUALIFICATION_SCHEMA,
            qualification_contract_version=1,
            qualification_reference=f"s012sq1:{qualification_digest}",
        )

    def _execute_read(
        self,
        credential: User,
        activity_id: UUID,
    ) -> ServiceActivityReadDTO:
        """Return the visibility-checked Activity record.

        This runs inside the public read's atomic block and
        opens none of its own. The Activity is asked for with a
        lock; absence raises ServiceActivityReadNotFound. The
        viewer's Identity for the credential is fetched next,
        with its credential joined and locked: a missing
        Identity, an Identity that is not active, and then an
        inactive credential each raise
        ServiceActivityReadNotAuthorised.

        The head is asked for if one is named, then all
        transitions by sequence and primary key, the first
        assignment and the first submission; where there is a
        submission, its first review; then all evidence by
        transition, kind and reference. These queries use
        select_for_update.

        Visibility is checked, the stored lineage validated, and
        only then the record and its history built. Visibility
        refusals and ServiceActivityReadLineageError propagate,
        as do other lookup, database, custody, clock and
        provider errors.
        """
        # 1. Lock Activity first
        try:
            activity = (
                ServiceActivity.objects.using(self._alias)
                .select_for_update()
                .get(activity_id=activity_id)
            )
        except ServiceActivity.DoesNotExist:
            raise ServiceActivityReadNotFound("ServiceActivity not found")

        # 2. Co-lock viewer Identity and credential
        try:
            viewer = (
                Identity.objects.using(self._alias)
                .select_for_update()
                .select_related("credential")
                .get(credential=credential)
            )
        except Identity.DoesNotExist:
            raise ServiceActivityReadNotAuthorised("viewer Identity not found")

        if viewer.access_state != Identity.AccessState.ACTIVE:
            raise ServiceActivityReadNotAuthorised("viewer Identity is not active")
        if not viewer.credential.is_active:
            raise ServiceActivityReadNotAuthorised("viewer credential is not active")

        # 3. Lock head_transition, all transitions, children, evidence
        head = None
        if activity.head_transition_id is not None:
            head = (
                ServiceActivityTransition.objects.using(self._alias)
                .select_for_update()
                .get(pk=activity.head_transition_id)
            )

        transitions = list(
            ServiceActivityTransition.objects.using(self._alias)
            .select_for_update()
            .select_related("actor")
            .filter(activity=activity)
            .order_by("sequence", "pk")
        )

        assignment = (
            ServiceActivityAssignment.objects.using(self._alias)
            .select_for_update()
            .select_related("assignee", "assigned_by")
            .filter(activity=activity)
            .first()
        )

        submission = (
            ServiceWorkSubmission.objects.using(self._alias)
            .select_for_update()
            .select_related("submitted_by")
            .filter(activity=activity)
            .first()
        )

        review = None
        if submission is not None:
            review = (
                ServiceActivityReview.objects.using(self._alias)
                .select_for_update()
                .select_related("reviewed_by")
                .filter(submission=submission)
                .first()
            )

        all_evidence = list(
            ServiceActivityEvidenceReference.objects.using(self._alias)
            .select_for_update()
            .select_related("supplied_by")
            .filter(transition__activity=activity)
            .order_by("transition_id", "evidence_kind", "reference")
        )

        # 4. Evaluate visibility
        self._check_visibility(viewer, activity, assignment)

        # 5. Validate complete lineage
        self._validate_lineage(
            activity, transitions, head, assignment, submission, review, all_evidence
        )

        # 6. Build DTO
        return self._build_dto(
            activity, transitions, assignment, submission, review, all_evidence
        )

    def _check_visibility(
        self,
        viewer: Identity,
        activity: ServiceActivity,
        assignment: ServiceActivityAssignment | None,
    ) -> None:
        """Return None when the viewer may read the Activity.

        The creator is admitted first. Where there is an
        assignment, a matching work holder is admitted too: for
        ordinary work that is the assignee itself, and for
        service work whose reference begins board: or
        committee:, custody is asked about this viewer at the
        statement's own time. With no custody rows, committee
        work falls back to the assignee and board work has no
        holder. Any other viewer goes on to the provider.

        The clock is called: its value must be a datetime whose
        tzinfo is present, and is converted to UTC; the offset
        itself is not checked. check_visibility is then called
        with request as a keyword. The answer must be a
        ServiceActivityVisibilityResponse whose alias, viewer,
        epoch, Activity and instant equal the request's, whose
        authority reference is text of 1 to 255 characters after
        stripping, and whose visible is True. That reference is
        then discarded.

        Every refusal raises ServiceActivityReadNotAuthorised.
        Errors from custody, the database, the clock, the
        conversion or the provider propagate.
        """
        # Creator direct visibility
        if viewer.pk == activity.created_by_id:
            return
        # Assignee direct visibility
        if assignment is not None and viewer.pk == work_holder(activity, assignment, self._alias, person=viewer.pk):
            return
        # Third-party: require injected provider visible=True
        evaluated_at = self._clock()
        if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
            raise ServiceActivityReadNotAuthorised("visibility evaluation clock error")
        evaluated_at = evaluated_at.astimezone(timezone.utc)

        request = ServiceActivityVisibilityRequest(
            database_alias=self._alias,
            viewer_identity_id=viewer.identity_id,
            viewer_access_epoch=viewer.access_epoch,
            activity_id=activity.activity_id,
            evaluated_at=evaluated_at,
        )
        response = self._visibility_provider.check_visibility(request=request)
        if not isinstance(response, ServiceActivityVisibilityResponse):
            raise ServiceActivityReadNotAuthorised("visibility denied")
        # Validate echo
        if (
            response.database_alias != request.database_alias
            or response.viewer_identity_id != request.viewer_identity_id
            or response.viewer_access_epoch != request.viewer_access_epoch
            or response.activity_id != request.activity_id
            or response.evaluated_at != request.evaluated_at
        ):
            raise ServiceActivityReadNotAuthorised("visibility response mismatch")
        if not isinstance(response.authority_reference, str):
            raise ServiceActivityReadNotAuthorised("visibility denied")
        ref = response.authority_reference.strip()
        if not ref or len(ref) > 255:
            raise ServiceActivityReadNotAuthorised("visibility denied")
        if response.visible is not True:
            raise ServiceActivityReadNotAuthorised("visibility denied")

    def _validate_lineage(
        self,
        activity: ServiceActivity,
        transitions: list[ServiceActivityTransition],
        head: ServiceActivityTransition | None,
        assignment: ServiceActivityAssignment | None,
        submission: ServiceWorkSubmission | None,
        review: ServiceActivityReview | None,
        all_evidence: list[ServiceActivityEvidenceReference],
    ) -> None:
        """Check the supplied Activity lineage. Return None.

        Require a nonempty chain. Its first sequence is 1,
        with no predecessor, no from_state and action CREATE.
        Every transition must belong to the Activity. In input
        order, check sequence, predecessor, allowed edge and
        state continuity. A terminal state must be last.
        The head must exist and match the last transition by
        key. The Activity state must match its to_state.

        Fetch the ServiceVersion with its Service, without
        requesting a row lock. This checks existence only.
        Then match created_at to the CREATE occurrence. The
        creator's identity is not checked against its actor.

        Check ASSIGN, SUBMIT_WORK and REVIEW_WORK in order.
        For each action present, require its supplied child,
        then exactly one such transition. Without the action,
        reject a supplied child. For assignment and submission,
        check transition, Activity, actor and occurrence time.
        Submission also needs the assignment. Check its
        submitter with work_holder at submitted_at. For review,
        check transition, reviewer and time, then require the
        submission and its matching key. There is no separate
        review-to-Activity check or search for extra children.

        For every evidence row check transition membership,
        occurrence time and supplier against its actor. Then
        check each action's evidence. CREATE, ASSIGN, DECLINE,
        REVIEW, COMPLETE and CANCEL each need one row of their
        prescribed kind. ACCEPT needs none. SUBMIT permits
        zero or more support rows and rejects other kinds.

        For each transition rebuild its target fingerprint,
        command, payload target and byte-sorted evidence.
        Compare the payload digest with the stored one. The
        next check compares the target computation with itself.
        Use that target in the rebuilt authority response and
        compare its decision reference, then compare the
        lineage reference. Matching references show agreement
        with the rebuilt inputs. No authority is exercised.

        Explicit inconsistencies raise
        ServiceActivityReadLineageError. Other errors propagate
        unchanged, including ServiceVersion's DoesNotExist, database
        errors, and errors from work_holder and
        decision_reference_for. Nothing is written.
        """
        if not transitions:
            raise ServiceActivityReadLineageError("no transitions found")

        # Build evidence index by transition pk
        evidence_by_transition: dict[int, list[ServiceActivityEvidenceReference]] = {}
        for ev in all_evidence:
            evidence_by_transition.setdefault(ev.transition_id, []).append(ev)

        # Validate consecutive sequence starting at 1
        first = transitions[0]
        if first.sequence != 1:
            raise ServiceActivityReadLineageError("initial sequence must be 1")
        if first.previous_transition_id is not None:
            raise ServiceActivityReadLineageError("initial transition must have no predecessor")
        if first.from_state is not None:
            raise ServiceActivityReadLineageError("CREATE from_state must be null")
        if first.action != ServiceCommandAction.CREATE.value:
            raise ServiceActivityReadLineageError("first transition must be CREATE")

        # All transitions must belong to this activity
        for t in transitions:
            if t.activity_id != activity.pk:
                raise ServiceActivityReadLineageError("transition belongs to wrong activity")

        # Validate chain
        prev_id = None
        expected_state = None
        transition_by_pk: dict[int, ServiceActivityTransition] = {}
        for i, t in enumerate(transitions):
            transition_by_pk[t.pk] = t
            if t.sequence != i + 1:
                raise ServiceActivityReadLineageError("non-consecutive sequence")
            if t.previous_transition_id != prev_id:
                raise ServiceActivityReadLineageError("broken predecessor chain")

            # Validate edge
            edge = (t.action, t.from_state, t.to_state)
            if edge not in _VALID_EDGES:
                raise ServiceActivityReadLineageError(
                    f"invalid transition edge: {edge}"
                )

            # Validate from_state matches expected
            if i == 0:
                if t.from_state is not None:
                    raise ServiceActivityReadLineageError("initial from_state must be null")
            else:
                if t.from_state != expected_state:
                    raise ServiceActivityReadLineageError("from_state does not match prior to_state")

            expected_state = t.to_state
            prev_id = t.pk

            # Terminal state must be last
            if ServiceActivityState(t.to_state) in TERMINAL_STATES:
                if i != len(transitions) - 1:
                    raise ServiceActivityReadLineageError("terminal state has successor")

        # Validate head matches last transition
        last_transition = transitions[-1]
        if head is None:
            raise ServiceActivityReadLineageError("head_transition is null but transitions exist")
        if head.pk != last_transition.pk:
            raise ServiceActivityReadLineageError("head_transition does not match last transition")

        # Validate Activity state matches head to_state
        if activity.state != last_transition.to_state:
            raise ServiceActivityReadLineageError("activity state does not match head to_state")

        # Validate ServiceVersion binding
        sv = (
            ServiceVersion.objects.using(self._alias)
            .select_related("service")
            .get(pk=activity.service_version_id)
        )

        # Validate Activity.created_at equals CREATE transition occurred_at
        if activity.created_at != first.occurred_at:
            raise ServiceActivityReadLineageError("activity created_at does not match CREATE occurred_at")

        # Validate assignment cardinality and relationships
        assign_transitions = [
            t for t in transitions if t.action == ServiceCommandAction.ASSIGN.value
        ]
        if assign_transitions:
            if assignment is None:
                raise ServiceActivityReadLineageError("ASSIGN transition exists without assignment row")
            if len(assign_transitions) != 1:
                raise ServiceActivityReadLineageError("multiple ASSIGN transitions")
            at = assign_transitions[0]
            if assignment.transition_id != at.pk:
                raise ServiceActivityReadLineageError("assignment transition binding mismatch")
            if assignment.activity_id != activity.pk:
                raise ServiceActivityReadLineageError("assignment belongs to wrong activity")
            if assignment.assigned_by_id != at.actor_id:
                raise ServiceActivityReadLineageError("assignment assigned_by mismatch")
            if assignment.assigned_at != at.occurred_at:
                raise ServiceActivityReadLineageError("assignment occurrence time mismatch")
        else:
            if assignment is not None:
                raise ServiceActivityReadLineageError("assignment exists without ASSIGN transition")

        # Validate submission cardinality and relationships
        submit_transitions = [
            t for t in transitions if t.action == ServiceCommandAction.SUBMIT_WORK.value
        ]
        if submit_transitions:
            if submission is None:
                raise ServiceActivityReadLineageError("SUBMIT transition exists without submission row")
            if len(submit_transitions) != 1:
                raise ServiceActivityReadLineageError("multiple SUBMIT transitions")
            st = submit_transitions[0]
            if submission.transition_id != st.pk:
                raise ServiceActivityReadLineageError("submission transition binding mismatch")
            if submission.activity_id != activity.pk:
                raise ServiceActivityReadLineageError("submission belongs to wrong activity")
            if submission.submitted_by_id != st.actor_id:
                raise ServiceActivityReadLineageError("submission submitted_by mismatch")
            if submission.submitted_at != st.occurred_at:
                raise ServiceActivityReadLineageError("submission occurrence time mismatch")
            # submitter must equal assignee
            if assignment is None:
                raise ServiceActivityReadLineageError("submission without assignment")
            if submission.submitted_by_id != work_holder(activity, assignment, self._alias, submission.submitted_at, person=submission.submitted_by_id):
                raise ServiceActivityReadLineageError("submitter is not the assignee")
        else:
            if submission is not None:
                raise ServiceActivityReadLineageError("submission exists without SUBMIT transition")

        # Validate review cardinality and relationships
        review_transitions = [
            t for t in transitions if t.action == ServiceCommandAction.REVIEW_WORK.value
        ]
        if review_transitions:
            if review is None:
                raise ServiceActivityReadLineageError("REVIEW transition exists without review row")
            if len(review_transitions) != 1:
                raise ServiceActivityReadLineageError("multiple REVIEW transitions")
            rt = review_transitions[0]
            if review.transition_id != rt.pk:
                raise ServiceActivityReadLineageError("review transition binding mismatch")
            if review.reviewed_by_id != rt.actor_id:
                raise ServiceActivityReadLineageError("review reviewed_by mismatch")
            if review.reviewed_at != rt.occurred_at:
                raise ServiceActivityReadLineageError("review occurrence time mismatch")
            if submission is None:
                raise ServiceActivityReadLineageError("review without submission")
            if review.submission_id != submission.pk:
                raise ServiceActivityReadLineageError("review submission binding mismatch")
        else:
            if review is not None:
                raise ServiceActivityReadLineageError("review exists without REVIEW transition")

        # Validate evidence: occurrence time, supplied_by, and per-transition sets
        for ev in all_evidence:
            t = transition_by_pk.get(ev.transition_id)
            if t is None:
                raise ServiceActivityReadLineageError("evidence references unknown transition")
            if ev.occurred_at != t.occurred_at:
                raise ServiceActivityReadLineageError("evidence occurrence time mismatch")
            if ev.supplied_by_id != t.actor_id:
                raise ServiceActivityReadLineageError("evidence supplied_by mismatch")

        # Validate per-transition evidence cardinality
        for t in transitions:
            t_evidence = evidence_by_transition.get(t.pk, [])
            action_val = t.action
            if action_val == ServiceCommandAction.CREATE.value:
                if len(t_evidence) != 1:
                    raise ServiceActivityReadLineageError("CREATE must have exactly 1 evidence")
                if t_evidence[0].evidence_kind != ServiceActivityEvidenceKind.ACTIVITY_BASIS.value:
                    raise ServiceActivityReadLineageError("CREATE evidence kind mismatch")
            elif action_val == ServiceCommandAction.ASSIGN.value:
                if len(t_evidence) != 1:
                    raise ServiceActivityReadLineageError("ASSIGN must have exactly 1 evidence")
                if t_evidence[0].evidence_kind != ServiceActivityEvidenceKind.ASSIGNMENT_BASIS.value:
                    raise ServiceActivityReadLineageError("ASSIGN evidence kind mismatch")
            elif action_val == ServiceCommandAction.ACCEPT_ASSIGNMENT.value:
                if len(t_evidence) != 0:
                    raise ServiceActivityReadLineageError("ACCEPT must have no evidence")
            elif action_val == ServiceCommandAction.DECLINE_ASSIGNMENT.value:
                if len(t_evidence) != 1:
                    raise ServiceActivityReadLineageError("DECLINE must have exactly 1 evidence")
                if t_evidence[0].evidence_kind != ServiceActivityEvidenceKind.DECLINE_BASIS.value:
                    raise ServiceActivityReadLineageError("DECLINE evidence kind mismatch")
            elif action_val == ServiceCommandAction.SUBMIT_WORK.value:
                for e in t_evidence:
                    if e.evidence_kind != ServiceActivityEvidenceKind.SUBMISSION_SUPPORT.value:
                        raise ServiceActivityReadLineageError("SUBMIT evidence kind mismatch")
            elif action_val == ServiceCommandAction.REVIEW_WORK.value:
                if len(t_evidence) != 1:
                    raise ServiceActivityReadLineageError("REVIEW must have exactly 1 evidence")
                if t_evidence[0].evidence_kind != ServiceActivityEvidenceKind.REVIEW_RECORD.value:
                    raise ServiceActivityReadLineageError("REVIEW evidence kind mismatch")
            elif action_val == ServiceCommandAction.COMPLETE_ACTIVITY.value:
                if len(t_evidence) != 1:
                    raise ServiceActivityReadLineageError("COMPLETE must have exactly 1 evidence")
                if t_evidence[0].evidence_kind != ServiceActivityEvidenceKind.COMPLETION_RECORD.value:
                    raise ServiceActivityReadLineageError("COMPLETE evidence kind mismatch")
            elif action_val == ServiceCommandAction.CANCEL_ACTIVITY.value:
                if len(t_evidence) != 1:
                    raise ServiceActivityReadLineageError("CANCEL must have exactly 1 evidence")
                if t_evidence[0].evidence_kind != ServiceActivityEvidenceKind.CANCELLATION_BASIS.value:
                    raise ServiceActivityReadLineageError("CANCEL evidence kind mismatch")

        # Recompute and validate cryptographic references for each transition
        for t in transitions:
            action_enum = ServiceCommandAction(t.action)
            actor_identity_id = t.actor.identity_id
            t_evidence = evidence_by_transition.get(t.pk, [])

            # Reconstruct target fingerprint
            target_fp = self._recompute_target_for_transition(
                action_enum, activity, assignment
            )

            # Reconstruct command dict from stored children/evidence
            command_dict = self._reconstruct_command_dict(
                action_enum, activity, assignment, submission, review, t_evidence
            )

            # Reconstruct target dict
            target_dict = self._reconstruct_target_dict(
                action_enum, activity, assignment
            )

            # Reconstruct sorted evidence dicts
            evidence_dicts = _sorted_evidence_dicts(t_evidence, actor_identity_id)

            # Recompute payload fingerprint
            expected_payload_fp = _recompute_payload_fingerprint(
                action_enum,
                actor_identity_id,
                t.actor_access_epoch,
                t.request_reference,
                t.idempotency_key,
                t.occurred_at,
                target_dict,
                command_dict,
                evidence_dicts,
            )
            if expected_payload_fp != t.payload_fingerprint:
                raise ServiceActivityReadLineageError("payload fingerprint mismatch")

            # Validate target fingerprint stored via decision reference
            if target_fp != self._recompute_target_for_transition(
                action_enum, activity, assignment
            ):
                raise ServiceActivityReadLineageError("target fingerprint mismatch")

            # Recompute decision reference
            expected_decision_ref = _recompute_decision_reference(
                self._alias,
                t.actor_id,
                actor_identity_id,
                t.actor_access_epoch,
                action_enum,
                target_fp,
                t.request_reference,
                t.idempotency_key,
                t.authority_evaluated_at,
                t.authority_reference,
            )
            if expected_decision_ref != t.authority_decision_reference:
                raise ServiceActivityReadLineageError("decision reference mismatch")

            # Recompute lineage reference
            expected_lineage_ref = _recompute_lineage_reference(
                activity.activity_id,
                t.sequence,
                action_enum,
                actor_identity_id,
                t.actor_access_epoch,
                expected_payload_fp,
                t.occurred_at,
            )
            if expected_lineage_ref != t.lineage_reference:
                raise ServiceActivityReadLineageError("lineage reference mismatch")

    def _recompute_target_for_transition(
        self,
        action: ServiceCommandAction,
        activity: ServiceActivity,
        assignment: ServiceActivityAssignment | None,
    ) -> str:
        """Return the authority target as a hex digest.

        All targets include the action, Activity ID and schema.
        CREATE adds service_version_id as service_version_pk.
        ASSIGN adds the assignee's identity ID as text and
        raises ServiceActivityReadLineageError if the assignment
        is absent. Other actions add no fields.
        """
        if action == ServiceCommandAction.CREATE:
            return _recompute_target_fingerprint(
                action,
                activity.activity_id,
                service_version_pk=activity.service_version_id,
            )
        elif action == ServiceCommandAction.ASSIGN:
            if assignment is None:
                raise ServiceActivityReadLineageError("ASSIGN target requires assignment")
            return _recompute_target_fingerprint(
                action,
                activity.activity_id,
                assignee_identity_id=str(assignment.assignee.identity_id),
            )
        else:
            return _recompute_target_fingerprint(action, activity.activity_id)

    def _reconstruct_target_dict(
        self,
        action: ServiceCommandAction,
        activity: ServiceActivity,
        assignment: ServiceActivityAssignment | None,
    ) -> dict:
        """Return the target mapping used to hash the payload.

        Start with the Activity ID as text. CREATE also carries
        service_version_pk. ASSIGN carries the assignee's
        identity ID as text and raises
        ServiceActivityReadLineageError without an assignment.
        Other actions carry just the Activity ID. This mapping
        has no action or schema field.
        """
        if action == ServiceCommandAction.CREATE:
            return {
                "activity_id": str(activity.activity_id),
                "service_version_pk": activity.service_version_id,
            }
        elif action == ServiceCommandAction.ASSIGN:
            if assignment is None:
                raise ServiceActivityReadLineageError("ASSIGN target requires assignment")
            return {
                "activity_id": str(activity.activity_id),
                "assignee_identity_id": str(assignment.assignee.identity_id),
            }
        else:
            return {"activity_id": str(activity.activity_id)}

    def _reconstruct_command_dict(
        self,
        action: ServiceCommandAction,
        activity: ServiceActivity,
        assignment: ServiceActivityAssignment | None,
        submission: ServiceWorkSubmission | None,
        review: ServiceActivityReview | None,
        t_evidence: list[ServiceActivityEvidenceReference],
    ) -> dict:
        """Return the command mapping for this action.

        Use the supplied rows. Where one evidence reference is
        required, filter by kind first and require one match.
        Other kinds are ignored here.

        CREATE returns the ACTIVITY_BASIS reference and the
        Activity's initiating domain and domain reference.
        ASSIGN first needs its assignment, then one
        ASSIGNMENT_BASIS. Return the assignee's identity ID as
        text, the basis reference and the assignment reference.
        ACCEPT_ASSIGNMENT returns an empty mapping immediately.
        DECLINE_ASSIGNMENT returns its one DECLINE_BASIS
        reference in the mapping.

        SUBMIT_WORK first needs its submission. Return its
        reference and a list of SUBMISSION_SUPPORT references
        in the order given. Both read entries fetch evidence
        ordered by transition, kind and reference. The list
        may be empty. REVIEW_WORK
        first needs its review, then one REVIEW_RECORD. Return
        that evidence reference and the review's own reference.
        COMPLETE_ACTIVITY returns one COMPLETION_RECORD
        reference. CANCEL_ACTIVITY returns one
        CANCELLATION_BASIS reference.

        A missing required child, wrong matching count or
        unknown action raises ServiceActivityReadLineageError.
        Related-object lookup errors propagate. This helper
        does not validate bindings or sort the support list.
        """
        if action == ServiceCommandAction.CREATE:
            basis_ev = [
                e for e in t_evidence
                if e.evidence_kind == ServiceActivityEvidenceKind.ACTIVITY_BASIS.value
            ]
            if len(basis_ev) != 1:
                raise ServiceActivityReadLineageError("CREATE evidence reconstruction failed")
            return {
                "activity_basis_reference": basis_ev[0].reference,
                "initiating_domain": activity.initiating_domain,
                "initiating_domain_reference": activity.initiating_domain_reference,
            }
        elif action == ServiceCommandAction.ASSIGN:
            if assignment is None:
                raise ServiceActivityReadLineageError("ASSIGN command reconstruction failed")
            basis_ev = [
                e for e in t_evidence
                if e.evidence_kind == ServiceActivityEvidenceKind.ASSIGNMENT_BASIS.value
            ]
            if len(basis_ev) != 1:
                raise ServiceActivityReadLineageError("ASSIGN evidence reconstruction failed")
            return {
                "assignee_identity_id": str(assignment.assignee.identity_id),
                "assignment_basis_reference": basis_ev[0].reference,
                "assignment_reference": assignment.assignment_reference,
            }
        elif action == ServiceCommandAction.ACCEPT_ASSIGNMENT:
            return {}
        elif action == ServiceCommandAction.DECLINE_ASSIGNMENT:
            decline_ev = [
                e for e in t_evidence
                if e.evidence_kind == ServiceActivityEvidenceKind.DECLINE_BASIS.value
            ]
            if len(decline_ev) != 1:
                raise ServiceActivityReadLineageError("DECLINE evidence reconstruction failed")
            return {"decline_basis_reference": decline_ev[0].reference}
        elif action == ServiceCommandAction.SUBMIT_WORK:
            if submission is None:
                raise ServiceActivityReadLineageError("SUBMIT command reconstruction failed")
            support_ev = [
                e for e in t_evidence
                if e.evidence_kind == ServiceActivityEvidenceKind.SUBMISSION_SUPPORT.value
            ]
            support_refs = [e.reference for e in support_ev]
            return {
                "submission_reference": submission.submission_reference,
                "submission_support_references": support_refs,
            }
        elif action == ServiceCommandAction.REVIEW_WORK:
            if review is None:
                raise ServiceActivityReadLineageError("REVIEW command reconstruction failed")
            record_ev = [
                e for e in t_evidence
                if e.evidence_kind == ServiceActivityEvidenceKind.REVIEW_RECORD.value
            ]
            if len(record_ev) != 1:
                raise ServiceActivityReadLineageError("REVIEW evidence reconstruction failed")
            return {
                "review_record_reference": record_ev[0].reference,
                "review_reference": review.review_reference,
            }
        elif action == ServiceCommandAction.COMPLETE_ACTIVITY:
            completion_ev = [
                e for e in t_evidence
                if e.evidence_kind == ServiceActivityEvidenceKind.COMPLETION_RECORD.value
            ]
            if len(completion_ev) != 1:
                raise ServiceActivityReadLineageError("COMPLETE evidence reconstruction failed")
            return {"completion_record_reference": completion_ev[0].reference}
        elif action == ServiceCommandAction.CANCEL_ACTIVITY:
            cancel_ev = [
                e for e in t_evidence
                if e.evidence_kind == ServiceActivityEvidenceKind.CANCELLATION_BASIS.value
            ]
            if len(cancel_ev) != 1:
                raise ServiceActivityReadLineageError("CANCEL evidence reconstruction failed")
            return {"cancellation_basis_reference": cancel_ev[0].reference}
        else:
            raise ServiceActivityReadLineageError(f"unknown action: {action}")

    def _build_dto(
        self,
        activity: ServiceActivity,
        transitions: list[ServiceActivityTransition],
        assignment: ServiceActivityAssignment | None,
        submission: ServiceWorkSubmission | None,
        review: ServiceActivityReview | None,
        all_evidence: list[ServiceActivityEvidenceReference],
    ) -> ServiceActivityReadDTO:
        """Return a ServiceActivityReadDTO for the given rows.

        Group evidence by transition key. In the given
        transition order, sort each local group by the UTF-8
        bytes of kind and then reference. Convert evidence to
        a tuple of kind and reference records, then build the
        history entry with sequence, action, states, actor
        identity, time and lineage reference. A null from_state
        stays None. Unmatched groups are unused. The input
        evidence list is unchanged.

        Build assignment, submission and review records, in
        that order, for supplied children. Absent children
        remain None. Fetch the ServiceVersion by the Activity's
        version key, then the Service by that version's Service
        key, on the service alias without requesting row locks.

        Convert the Activity state, then look up its fixed
        message. Return the IDs and Service state, whether the
        Service's current version key equals the fetched version
        key, the initiating domain and reference, Activity state
        and message, child records and history tuple.

        Invalid evidence kinds, actions, states or initiating
        domains raise ValueError during enum conversion. A
        missing state message raises KeyError. ServiceVersion
        or Service DoesNotExist and other lookup or database
        errors propagate. This builds on the caller's lineage
        check. It writes nothing.
        """
        # Build evidence index by transition pk
        evidence_by_transition: dict[int, list[ServiceActivityEvidenceReference]] = {}
        for ev in all_evidence:
            evidence_by_transition.setdefault(ev.transition_id, []).append(ev)

        # Build history
        history_entries: list[ServiceActivityHistoryEntryDTO] = []
        for t in transitions:
            t_evidence = evidence_by_transition.get(t.pk, [])
            # Sort evidence by (kind, reference) using UTF-8 bytes
            t_evidence.sort(
                key=lambda e: (
                    e.evidence_kind.encode("utf-8"),
                    e.reference.encode("utf-8"),
                )
            )
            evidence_dtos = tuple(
                ServiceActivityEvidenceDTO(
                    evidence_kind=ServiceActivityEvidenceKind(e.evidence_kind),
                    reference=e.reference,
                )
                for e in t_evidence
            )
            from_state = (
                ServiceActivityState(t.from_state) if t.from_state is not None else None
            )
            history_entries.append(
                ServiceActivityHistoryEntryDTO(
                    sequence=t.sequence,
                    action=ServiceCommandAction(t.action),
                    from_state=from_state,
                    to_state=ServiceActivityState(t.to_state),
                    actor_identity_id=t.actor.identity_id,
                    occurred_at=t.occurred_at,
                    lineage_reference=t.lineage_reference,
                    evidence=evidence_dtos,
                )
            )

        # Build child DTOs
        assignment_dto = None
        if assignment is not None:
            assignment_dto = ServiceActivityAssignmentDTO(
                assignee_identity_id=assignment.assignee.identity_id,
                assigned_by_identity_id=assignment.assigned_by.identity_id,
                assignment_reference=assignment.assignment_reference,
                assigned_at=assignment.assigned_at,
            )

        submission_dto = None
        if submission is not None:
            submission_dto = ServiceWorkSubmissionDTO(
                submitted_by_identity_id=submission.submitted_by.identity_id,
                submission_reference=submission.submission_reference,
                submitted_at=submission.submitted_at,
            )

        review_dto = None
        if review is not None:
            review_dto = ServiceActivityReviewDTO(
                reviewed_by_identity_id=review.reviewed_by.identity_id,
                review_reference=review.review_reference,
                reviewed_at=review.reviewed_at,
            )

        # Service context
        sv = ServiceVersion.objects.using(self._alias).get(pk=activity.service_version_id)
        service = Service.objects.using(self._alias).get(pk=sv.service_id)

        state = ServiceActivityState(activity.state)
        state_message = _STATE_MESSAGES[activity.state]

        return ServiceActivityReadDTO(
            activity_id=activity.activity_id,
            service_id=service.pk,
            service_version_id=sv.pk,
            service_state=service.state,
            service_version_is_current=(service.current_version_id == sv.pk),
            initiating_domain=InitiatingDomain(activity.initiating_domain),
            initiating_domain_reference=activity.initiating_domain_reference,
            state=state,
            state_message=state_message,
            assignment=assignment_dto,
            work_submission=submission_dto,
            review=review_dto,
            history=tuple(history_entries),
        )


__all__ = [
    "ServiceActivityAssignmentDTO",
    "ServiceActivityEvidenceDTO",
    "ServiceActivityHistoryEntryDTO",
    "ServiceActivityReadDTO",
    "ServiceActivityReadError",
    "ServiceActivityReadLineageError",
    "ServiceActivityReadNotAuthorised",
    "ServiceActivityReadNotFound",
    "ServiceActivityReadService",
    "ServiceActivityReviewDTO",
    "ServiceActivityVisibilityProvider",
    "ServiceActivityVisibilityRequest",
    "ServiceActivityVisibilityResponse",
    "ServiceWorkSubmissionDTO",
]
