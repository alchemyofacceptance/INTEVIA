"""Governed positive-presence attendance commands and immutable lineage."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from core.models import (
    Event,
    EventAttendance,
    EventAttendanceEligibilityReceipt,
    EventAttendanceEvidenceReference,
    EventAttendanceTransition,
    EventRegistration,
    Identity,
)
from src.intevia.services.clock import database_now


class EventAttendanceError(Exception):
    """Base failure for governed Event attendance."""


class AttendanceNotAuthorised(EventAttendanceError):
    """Attendance authority was refused for the actor or action.

    Reasons include an inactive credential or Identity, an unresolved
    Identity, self-attestation on first record, a denied or unusable
    reference, or changed actor details or access at recheck.
    """
    pass


class InvalidAttendance(EventAttendanceError):
    """The request breaks a governed attendance rule: its evidence,
    origin, registration, walk-in basis or the Event's state."""
    pass


class DuplicateEventAttendance(EventAttendanceError):
    """Attendance already exists for this Event and subject; each pair
    has at most one attendance record."""
    pass


class AttendanceIdempotencyConflict(EventAttendanceError):
    """This actor already used the idempotency key for this action with
    a different payload."""
    pass


class InvalidAttendanceTransition(EventAttendanceError):
    """The attendance is not in the status the action starts from: a
    correction or a void needs PRESENT, a reinstatement VOIDED."""
    pass


@dataclass(frozen=True, slots=True)
class AttendanceEvidence:
    """One evidence reference supplied with an attendance command.

    Only ACTOR_ATTESTATION is accepted. The service strips reference,
    evidence_type and provenance and requires each; nothing is checked
    when the value is built.
    """
    reference: str
    evidence_type: str
    provenance: str
    classification: str = (
        EventAttendanceEvidenceReference.Classification.ACTOR_ATTESTATION
    )


@dataclass(frozen=True, slots=True)
class WalkInEligibility:
    """The basis for a governed walk-in: a reference and its type. Only
    GOVERNED_WALK_IN is accepted; the reference is stripped and required
    when the attendance is recorded."""
    basis_reference: str
    basis_type: str = (
        EventAttendanceEligibilityReceipt.BasisType.GOVERNED_WALK_IN
    )


@dataclass(frozen=True, slots=True)
class EventAttendanceAuthorityTarget:
    """What the capability is asked to authorise.

    The actor, the authority action (for example record_attendance or
    historical_void_attendance), the Event, subject and origin, the
    requested outcome, the registration, attendance and prior
    transition, and the states read when the target was built.
    observation_time is the server time for a record, the requested
    time for a correction and the stored time for a void or
    reinstatement. actor_access_epoch is the epoch the locked actor
    must still have when the authority is checked.
    """
    actor: Identity
    action: str
    event: Event
    subject: Identity
    origin: str
    requested_outcome: str
    supporting_registration: EventRegistration | None
    attendance: EventAttendance | None
    prior_transition: EventAttendanceTransition | None
    observation_time: datetime
    event_state: str
    registration_state: str | None
    attendance_status: str | None
    actor_access_state: str
    actor_access_epoch: int


class EventAttendanceCapability(Protocol):
    """The local, synchronous decision that authorises an attendance
    action. EventAttendanceAuthority calls it and checks its result."""
    def authorise(
        self,
        *,
        identity: Identity,
        action: str,
        target: EventAttendanceAuthorityTarget,
        timestamp: datetime,
    ) -> str | None:
        """Return an authority reference for the actor's Identity.

        The evaluator raises TypeError for an awaitable result.
        It raises AttendanceNotAuthorised for any other non-string,
        including None. A string must have 1 to 255 characters after
        stripping or the evaluator raises AttendanceNotAuthorised.
        """
        ...


class EventAttendanceAuthority:
    """Attendance-specific local capability evaluation and actor recheck."""

    def __init__(self, capability: EventAttendanceCapability) -> None:
        """Keep the capability. TypeError if it has no callable authorise or
        authorise is a coroutine function. Its signature is not checked."""
        if not callable(getattr(capability, "authorise", None)):
            raise TypeError("capability must provide authorise")
        if inspect.iscoroutinefunction(capability.authorise):
            raise TypeError("attendance capability must be local and synchronous")
        self.capability = capability

    @staticmethod
    def resolve_actor(identity: User) -> Identity:
        """Resolve an active Django User to its ACTIVE Identity.

        Require an active User, look up its Identity by credential,
        then require ACTIVE access. A wrong or inactive User, a missing
        or multiple match, or inactive access raises
        AttendanceNotAuthorised. Other lookup errors propagate.

        Return the Identity. This lookup takes no lock and uses the
        supplied User's active flag without refreshing the credential.
        """
        if not isinstance(identity, User) or not identity.is_active:
            raise AttendanceNotAuthorised("an active credential is required")
        try:
            actor = Identity.objects.get(credential=identity)
        except (Identity.DoesNotExist, Identity.MultipleObjectsReturned) as exc:
            raise AttendanceNotAuthorised(
                "credential must resolve to exactly one Identity"
            ) from exc
        if actor.access_state != Identity.AccessState.ACTIVE:
            raise AttendanceNotAuthorised("an active Identity is required")
        return actor

    def evaluate(
        self,
        *,
        identity: User,
        actor: Identity,
        action: str,
        target: EventAttendanceAuthorityTarget,
        timestamp: datetime,
    ) -> tuple[Identity, str]:
        """Ask the capability, then lock and re-check the actor.

        Calls authorise with the actor's Identity, the action, the target
        and the timestamp. An awaitable result raises TypeError; it is not
        awaited. A non-string result is a denial, and a reference that is
        blank or longer than 255 characters after stripping is refused;
        both raise AttendanceNotAuthorised.

        Then locks the actor's Identity row, with its credential row, by
        key, identity_id and this credential. AttendanceNotAuthorised if it
        no longer matches, is not ACTIVE, has an inactive credential, or
        has an access epoch other than the target's. Returns the locked
        Identity and the stripped reference.

        The capability decides before the lock, on the target as built; the
        lock re-checks only the actor's access.
        """
        authority_reference = self.capability.authorise(
            identity=actor,
            action=action,
            target=target,
            timestamp=timestamp,
        )
        if inspect.isawaitable(authority_reference):
            raise TypeError("attendance capability must be local and synchronous")
        if not isinstance(authority_reference, str):
            raise AttendanceNotAuthorised("attendance authority denied")
        authority_reference = authority_reference.strip()
        if not authority_reference or len(authority_reference) > 255:
            raise AttendanceNotAuthorised(
                "attendance authority reference is not durable"
            )

        try:
            locked_actor = Identity.objects.select_for_update().select_related(
                "credential"
            ).get(
                pk=actor.pk,
                identity_id=actor.identity_id,
                credential_id=identity.pk,
            )
        except Identity.DoesNotExist as exc:
            raise AttendanceNotAuthorised("actor identity changed") from exc
        if (
            locked_actor.access_state != Identity.AccessState.ACTIVE
            or not locked_actor.credential.is_active
            or locked_actor.access_epoch != target.actor_access_epoch
        ):
            raise AttendanceNotAuthorised("actor access changed")
        return locked_actor, authority_reference


class EventAttendanceService:
    """Persist Event-owned positive-presence assertions under authority."""

    _UNRECORDED = "unrecorded"

    def __init__(
        self,
        *,
        authority: EventAttendanceAuthority,
        clock: Callable[[], datetime] = database_now,
    ) -> None:
        """Keep the authority and the clock. TypeError unless authority is
        an EventAttendanceAuthority. clock defaults to database_now, the
        INTEVIA Clock on the default database; it is not called here,
        and each use checks that its value is aware."""
        if not isinstance(authority, EventAttendanceAuthority):
            raise TypeError("authority must be EventAttendanceAuthority")
        self.authority = authority
        self.clock = clock

    @staticmethod
    def _text(value: str, name: str) -> str:
        """Return the value stripped. ValidationError naming the field if it
        is not a string or is blank. Length is not checked here; the model's
        field limits apply when a row is saved."""
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{name} is required")
        return value.strip()

    @staticmethod
    def _at(value: datetime, name: str) -> datetime:
        """Return the same datetime if it is timezone-aware.

        If tzinfo or the UTC offset is None, raise ValidationError
        naming the field. Missing the tzinfo attribute raises
        AttributeError.
        """
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError(f"{name} must be timezone-aware")
        return value

    def _now(self) -> datetime:
        """Read the clock once and return its timezone-aware datetime.

        A naive value raises ValidationError. This is command time.
        A correction supplies the new observation time, and a void or
        reinstatement uses the attendance's stored observation time.
        """
        return self._at(self.clock(), "server observation time")

    @staticmethod
    def _fingerprint(payload: dict[str, object]) -> str:
        """Hash compact, key-sorted UTF-8 JSON with SHA-256.

        Return the hex digest. JSON uses ASCII escapes. Callers encode
        times as strings. JSON encoding errors propagate to the caller.
        """
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _evidence_payload(
        cls, evidence: Iterable[AttendanceEvidence]
    ) -> tuple[AttendanceEvidence, ...]:
        """Return a tuple of normalised evidence in input order.

        Collect the iterable. An empty collection raises
        InvalidAttendance. For each item, require AttendanceEvidence
        (ValidationError), then ACTOR_ATTESTATION (InvalidAttendance).
        Check and strip reference, evidence_type and provenance in that
        order. A non-string or blank field raises ValidationError.

        Return new items. Length and uniqueness are checked at save.
        A repeated reference on one transition raises IntegrityError
        if the insert reaches the database.
        """
        values = tuple(evidence)
        if not values:
            raise InvalidAttendance("actor attestation is required")
        normalised = []
        for item in values:
            if not isinstance(item, AttendanceEvidence):
                raise ValidationError("attendance evidence must be typed")
            if (
                item.classification
                != EventAttendanceEvidenceReference.Classification.ACTOR_ATTESTATION
            ):
                raise InvalidAttendance(
                    "attendance evidence must be actor attestation"
                )
            normalised.append(
                AttendanceEvidence(
                    reference=cls._text(item.reference, "evidence reference"),
                    evidence_type=cls._text(item.evidence_type, "evidence type"),
                    provenance=cls._text(item.provenance, "evidence provenance"),
                    classification=item.classification,
                )
            )
        return tuple(normalised)

    @staticmethod
    def _evidence_fingerprint(
        evidence: tuple[AttendanceEvidence, ...],
    ) -> list[dict[str, str]]:
        """Return the evidence as plain dicts sorted by classification,
        type, provenance and reference, so the fingerprint does not depend
        on the order supplied. Repeats are kept."""
        return sorted(
            (
                {
                    "classification": item.classification,
                    "evidence_type": item.evidence_type,
                    "provenance": item.provenance,
                    "reference": item.reference,
                }
                for item in evidence
            ),
            key=lambda item: (
                item["classification"],
                item["evidence_type"],
                item["provenance"],
                item["reference"],
            ),
        )

    @staticmethod
    def _constraint_name(error: IntegrityError) -> str | None:
        """Name the constraint an IntegrityError broke, or return None.

        PostgreSQL: the driver's diagnostic constraint_name on the error's
        cause. Otherwise the message is matched against the SQLite column
        lists of two constraints only, unique_event_subject_attendance and
        unique_attendance_idempotency; any other gives None.
        """
        diagnostics = getattr(error.__cause__, "diag", None)
        name = getattr(diagnostics, "constraint_name", None)
        if name:
            return name
        message = str(error)
        sqlite_constraints = {
            "core_eventattendance.event_id, core_eventattendance.subject_id": "unique_event_subject_attendance",
            "core_eventattendancetransition.actor_id, core_eventattendancetransition.action, core_eventattendancetransition.idempotency_key": "unique_attendance_idempotency",
        }
        return next(
            (name for columns, name in sqlite_constraints.items() if columns in message),
            None,
        )

    @staticmethod
    def _replay(
        *,
        actor: Identity,
        action: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> EventAttendanceTransition | None:
        """Look up the transition by actor, action and idempotency key.

        Return None on a miss. A different payload fingerprint raises
        AttendanceIdempotencyConflict. A match returns the stored
        transition with the attendance as read by this query.

        This lookup takes no row lock and makes no authority decision.
        """
        transition = (
            EventAttendanceTransition.objects.select_related("attendance")
            .filter(
                actor=actor,
                action=action,
                idempotency_key=idempotency_key,
            )
            .first()
        )
        if transition is None:
            return None
        if transition.payload_fingerprint != fingerprint:
            raise AttendanceIdempotencyConflict(
                "idempotency key was used with a different attendance payload"
            )
        return transition

    @staticmethod
    def _replay_candidate(
        *,
        actor: Identity,
        action: str,
        idempotency_key: str,
    ) -> EventAttendanceTransition | None:
        """Return this actor's earlier transition for the action and key, or
        None, without comparing payloads. Corrections, voids and
        reinstatements put its previous transition in the payload, so that a
        retry is fingerprinted against the same prior as the first request.
        """
        return EventAttendanceTransition.objects.filter(
            actor=actor,
            action=action,
            idempotency_key=idempotency_key,
        ).first()

    @staticmethod
    def _lock_registration(
        registration: EventRegistration | None,
        *,
        event: Event,
        subject: Identity,
        require_active: bool = True,
    ) -> EventRegistration | None:
        """Lock the matching registration. Return None if none is given.

        Match the registration key, Event and participant against the
        supplied registration key, Event and subject. A missing match
        raises InvalidAttendance.
        Only the registration row is locked by this lookup.

        By default, a state other than REGISTERED raises
        InvalidAttendance. Void and reinstatement skip the state check.
        Return the locked registration on success.
        """
        if registration is None:
            return None
        try:
            locked = EventRegistration.objects.select_for_update().get(
                pk=registration.pk,
                event=event,
                participant=subject,
            )
        except EventRegistration.DoesNotExist as exc:
            raise InvalidAttendance(
                "supporting registration must match Event and subject"
            ) from exc
        if require_active and locked.state != EventRegistration.State.REGISTERED:
            raise InvalidAttendance("supporting registration must be active")
        return locked

    @staticmethod
    def _target(
        *,
        actor: Identity,
        action: str,
        event: Event,
        subject: Identity,
        origin: str,
        requested_outcome: str,
        registration: EventRegistration | None,
        attendance: EventAttendance | None,
        prior: EventAttendanceTransition | None,
        observed_at: datetime,
    ) -> EventAttendanceAuthorityTarget:
        """Build the authority target from the objects given. The Event,
        registration, attendance and actor states and the actor's access
        epoch are read from those objects as passed; nothing is re-read or
        locked here."""
        return EventAttendanceAuthorityTarget(
            actor=actor,
            action=action,
            event=event,
            subject=subject,
            origin=origin,
            requested_outcome=requested_outcome,
            supporting_registration=registration,
            attendance=attendance,
            prior_transition=prior,
            observation_time=observed_at,
            event_state=event.state,
            registration_state=registration.state if registration else None,
            attendance_status=attendance.status if attendance else None,
            actor_access_state=actor.access_state,
            actor_access_epoch=actor.access_epoch,
        )

    @staticmethod
    def _persist_evidence(
        *,
        transition: EventAttendanceTransition,
        actor: Identity,
        evidence: tuple[AttendanceEvidence, ...],
        supplied_at: datetime,
    ) -> None:
        """Write one evidence row per item for the transition, supplied by
        the actor at the given time. Each row is validated when saved (field
        limits give ValidationError); a reference repeated within the
        transition breaks a unique constraint (IntegrityError)."""
        for item in evidence:
            EventAttendanceEvidenceReference.objects.create(
                transition=transition,
                evidence_type=item.evidence_type,
                classification=item.classification,
                reference=item.reference,
                provenance=item.provenance,
                supplied_by=actor,
                supplied_at=supplied_at,
            )

    @transaction.atomic
    def record_attendance(
        self,
        *,
        identity: User,
        attendance_id: str,
        event_id: str,
        subject: Identity,
        origin: str,
        evidence: Iterable[AttendanceEvidence],
        idempotency_key: str,
        supporting_registration: EventRegistration | None = None,
        walk_in_eligibility: WalkInEligibility | None = None,
    ) -> EventAttendance:
        """Record first presence atomically and return the attendance
        row.

        Read the aware command clock once for observation and authority.
        Strip attendance ID, Event ID and key. Require typed actor
        attestation evidence. Blank or non-string text, a naive time or
        an untyped evidence item raises ValidationError. Empty evidence
        or another classification raises InvalidAttendance.

        Reject an unknown origin with InvalidAttendance. Require a
        stored Identity matching the subject's PK and identity_id
        (ValidationError). Resolve the active actor without a lock.
        Failed resolution or self-attestation raises
        AttendanceNotAuthorised. Subject access state is not an entry
        condition.

        REGISTERED takes a registration alone. A walk-in takes only
        WalkInEligibility with GOVERNED_WALK_IN basis. A wrong
        combination or basis type raises InvalidAttendance. Strip the
        walk-in basis reference as required text (ValidationError). The
        service does not independently verify the eligibility claim.

        Match replay by actor, RECORD, key and fingerprint. Use sorted
        evidence and a server-time marker in the payload. A changed
        payload raises AttendanceIdempotencyConflict. An early match
        returns the attendance as read now, before Event or registration
        checks and without a new capability or locked actor check.

        Lock the Event (unknown ID raises Event.DoesNotExist). Lock the
        supplied registration if present. A missing match on PK, Event
        and subject, or a state other than REGISTERED, raises
        InvalidAttendance. Lock any attendance for this Event and
        subject. Require an ACTIVE Event (InvalidAttendance), retry
        replay, then raise DuplicateEventAttendance if the pair already
        exists.

        Ask for record_attendance. evaluate checks the reference, then
        locks and rechecks actor and credential. Denial or changed
        access raises AttendanceNotAuthorised. An awaitable raises
        TypeError. The subject lookup takes no separate lock.

        Write PRESENT attendance, sequence-one RECORD, each evidence row
        and, for a walk-in, its eligibility receipt in one savepoint.
        Model validation can raise ValidationError, including a reused
        attendance ID detected at validation. On an idempotency
        constraint collision, retry replay. Map an Event/subject
        constraint collision to DuplicateEventAttendance. An unmatched
        IntegrityError propagates, including a repeated evidence
        reference.
        """
        recorded_at = self._now()
        attendance_id = self._text(attendance_id, "attendance_id")
        event_id = self._text(event_id, "event_id")
        idempotency_key = self._text(idempotency_key, "idempotency_key")
        evidence = self._evidence_payload(evidence)
        if origin not in EventAttendance.Origin.values:
            raise InvalidAttendance("attendance origin is not governed")
        if not isinstance(subject, Identity) or subject.pk is None:
            raise ValidationError("subject must be a persisted Identity")
        if not Identity.objects.filter(
            pk=subject.pk,
            identity_id=subject.identity_id,
        ).exists():
            raise ValidationError("subject must be a durable Identity")
        actor = self.authority.resolve_actor(identity)
        if actor.pk == subject.pk:
            raise AttendanceNotAuthorised("attendance cannot be self-attested")

        if origin == EventAttendance.Origin.REGISTERED:
            if supporting_registration is None or walk_in_eligibility is not None:
                raise InvalidAttendance(
                    "registered attendance requires only an exact registration"
                )
        else:
            if supporting_registration is not None:
                raise InvalidAttendance("walk-in attendance cannot use registration")
            if not isinstance(walk_in_eligibility, WalkInEligibility):
                raise InvalidAttendance("walk-in eligibility receipt is required")
            if (
                walk_in_eligibility.basis_type
                != EventAttendanceEligibilityReceipt.BasisType.GOVERNED_WALK_IN
            ):
                raise InvalidAttendance("walk-in eligibility basis is not governed")

        payload = {
            "action": EventAttendanceTransition.Action.RECORD,
            "attendance_id": attendance_id,
            "event_id": event_id,
            "subject_id": subject.pk,
            "origin": origin,
            "supporting_registration_id": (
                supporting_registration.pk if supporting_registration else None
            ),
            "observation_time": "server_generated",
            "requested_outcome": EventAttendance.Status.PRESENT,
            "prior_transition_id": None,
            "evidence": self._evidence_fingerprint(evidence),
            "walk_in_basis_type": (
                walk_in_eligibility.basis_type if walk_in_eligibility else None
            ),
            "walk_in_basis_reference": (
                self._text(
                    walk_in_eligibility.basis_reference,
                    "walk-in basis reference",
                )
                if walk_in_eligibility
                else None
            ),
        }
        fingerprint = self._fingerprint(payload)
        replay = self._replay(
            actor=actor,
            action=EventAttendanceTransition.Action.RECORD,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay.attendance

        event = Event.objects.select_for_update().get(event_id=event_id)
        registration = self._lock_registration(
            supporting_registration,
            event=event,
            subject=subject,
        )
        existing = EventAttendance.objects.select_for_update().filter(
            event=event,
            subject=subject,
        ).first()
        if event.state != Event.State.ACTIVE:
            raise InvalidAttendance(
                f"first attendance is not permitted while Event is {event.state}"
            )
        replay = self._replay(
            actor=actor,
            action=EventAttendanceTransition.Action.RECORD,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay.attendance
        if existing is not None:
            raise DuplicateEventAttendance(
                "attendance already exists for this Event and subject"
            )

        target = self._target(
            actor=actor,
            action="record_attendance",
            event=event,
            subject=subject,
            origin=origin,
            requested_outcome=EventAttendance.Status.PRESENT,
            registration=registration,
            attendance=None,
            prior=None,
            observed_at=recorded_at,
        )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            actor=actor,
            action="record_attendance",
            target=target,
            timestamp=recorded_at,
        )

        try:
            with transaction.atomic():
                attendance = EventAttendance.objects.create(
                    attendance_id=attendance_id,
                    event=event,
                    subject=subject,
                    status=EventAttendance.Status.PRESENT,
                    observed_at=recorded_at,
                    supporting_registration=registration,
                    origin=origin,
                )
                transition = EventAttendanceTransition.objects.create(
                    attendance=attendance,
                    sequence=1,
                    action=EventAttendanceTransition.Action.RECORD,
                    from_status=self._UNRECORDED,
                    to_status=EventAttendance.Status.PRESENT,
                    actor=actor,
                    authority_reference=authority_reference,
                    authority_evaluated_at=recorded_at,
                    origin=origin,
                    previous_observed_at=None,
                    resulting_observed_at=recorded_at,
                    previous_supporting_registration=None,
                    resulting_supporting_registration=registration,
                    basis=origin,
                    idempotency_key=idempotency_key,
                    payload_fingerprint=fingerprint,
                    recorded_at=recorded_at,
                )
                self._persist_evidence(
                    transition=transition,
                    actor=actor,
                    evidence=evidence,
                    supplied_at=recorded_at,
                )
                if walk_in_eligibility is not None:
                    EventAttendanceEligibilityReceipt.objects.create(
                        attendance=attendance,
                        record_transition=transition,
                        basis_type=walk_in_eligibility.basis_type,
                        basis_reference=payload["walk_in_basis_reference"],
                        event=event,
                        subject=subject,
                        actor=actor,
                        authority_reference=authority_reference,
                        event_state_snapshot=event.state,
                        evaluated_at=recorded_at,
                    )
        except IntegrityError as error:
            constraint = self._constraint_name(error)
            if constraint == "unique_attendance_idempotency":
                replay = self._replay(
                    actor=actor,
                    action=EventAttendanceTransition.Action.RECORD,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                )
                if replay is not None:
                    return replay.attendance
            if constraint == "unique_event_subject_attendance":
                raise DuplicateEventAttendance(
                    "attendance already exists for this Event and subject"
                ) from error
            raise
        return attendance

    @transaction.atomic
    def correct_attendance(
        self,
        *,
        identity: User,
        attendance_id: str,
        resulting_observed_at: datetime,
        rationale: str,
        evidence: Iterable[AttendanceEvidence],
        idempotency_key: str,
        resulting_supporting_registration: EventRegistration | None = None,
    ) -> EventAttendanceTransition:
        """Correct the observed time and registration in one
        transaction.

        Supply an aware resulting_observed_at even for a registration
        change. A naive datetime raises ValidationError before the clock
        or attendance is read. Missing the tzinfo attribute raises
        AttributeError. For REGISTERED attendance, supply the resulting
        registration even to retain the current one. For a walk-in, omit
        it or pass None.

        Delegate locks, evidence, authority and writes to _correct. It
        requires PRESENT and checks the registration before replay.
        Return the new CORRECT transition or its matching stored
        transition.
        """
        return self._correct(
            identity=identity,
            attendance_id=attendance_id,
            resulting_observed_at=self._at(
                resulting_observed_at,
                "resulting_observed_at",
            ),
            resulting_supporting_registration=resulting_supporting_registration,
            rationale=rationale,
            evidence=evidence,
            idempotency_key=idempotency_key,
        )

    def _correct(
        self,
        *,
        identity: User,
        attendance_id: str,
        resulting_observed_at: datetime,
        resulting_supporting_registration: EventRegistration | None,
        rationale: str,
        evidence: Iterable[AttendanceEvidence],
        idempotency_key: str,
    ) -> EventAttendanceTransition:
        """Append a CORRECT transition in the caller's transaction.

        Read the aware command clock. Strip attendance ID, rationale and
        key, validate the attestation evidence, then resolve the active
        actor. Invalid text, a naive clock or untyped evidence raises
        ValidationError. Empty or wrongly classified evidence raises
        InvalidAttendance. Actor refusal raises AttendanceNotAuthorised.

        Read attendance, Event and subject without a lock. An unknown ID
        raises EventAttendance.DoesNotExist. Lock the Event, then
        matching rows for the snapshot and proposed registration PKs in
        PK order, then the attendance and its latest transition. There
        is no separate subject lock.

        Require PRESENT before replay (InvalidAttendanceTransition). For
        REGISTERED, require an explicit matching Event/subject
        registration. A replacement must be REGISTERED. The retained
        registration may be in any state. A walk-in cannot gain one.
        Registration failures raise InvalidAttendance.

        For this actor, CORRECT and key, fingerprint the requested
        facts, rationale and evidence against the original predecessor
        on a retry. A mismatch raises AttendanceIdempotencyConflict. A
        match returns the stored transition before capability evaluation
        or an epoch recheck.

        For a fresh correction ask for correct_attendance, prefixed with
        historical_ only on an ARCHIVED Event. Pass the requested
        observation time in the target and command time to the
        capability. This body imposes no Event-state or observation-time
        window and no separate self-attestation check. evaluate checks
        the reference, then locks and rechecks actor and credential.
        Denial or access failure raises AttendanceNotAuthorised. An
        awaitable raises TypeError.

        Append the next linked transition with old and new facts, the
        same status, rationale and authority. Write the evidence, then
        update observed time, registration and updated_at by queryset.
        Return the transition. Save validation can raise
        ValidationError. A database IntegrityError propagates, including
        duplicate evidence.
        """
        recorded_at = self._now()
        attendance_id = self._text(attendance_id, "attendance_id")
        rationale = self._text(rationale, "rationale")
        idempotency_key = self._text(idempotency_key, "idempotency_key")
        evidence = self._evidence_payload(evidence)
        actor = self.authority.resolve_actor(identity)
        snapshot = EventAttendance.objects.select_related("event", "subject").get(
            attendance_id=attendance_id
        )
        event = Event.objects.select_for_update().get(pk=snapshot.event_id)
        registration_ids = {
            value
            for value in (
                snapshot.supporting_registration_id,
                (
                    resulting_supporting_registration.pk
                    if resulting_supporting_registration
                    else None
                ),
            )
            if value is not None
        }
        locked_registrations = {
            registration.pk: registration
            for registration in EventRegistration.objects.select_for_update()
            .filter(pk__in=registration_ids)
            .order_by("pk")
        }
        attendance = EventAttendance.objects.select_for_update().get(pk=snapshot.pk)
        prior = attendance.transitions.select_for_update().order_by(
            "sequence"
        ).last()
        if attendance.status != EventAttendance.Status.PRESENT:
            raise InvalidAttendanceTransition("only present attendance can be corrected")
        if attendance.origin == EventAttendance.Origin.REGISTERED:
            if resulting_supporting_registration is None:
                raise InvalidAttendance(
                    "registered attendance must retain a registration"
                )
            registration = locked_registrations.get(
                resulting_supporting_registration.pk
            )
            if (
                registration is None
                or registration.event_id != event.pk
                or registration.participant_id != attendance.subject_id
                or (
                    registration.pk != attendance.supporting_registration_id
                    and registration.state != EventRegistration.State.REGISTERED
                )
            ):
                raise InvalidAttendance(
                    "corrected registration must be active for Event and subject"
                )
        else:
            if resulting_supporting_registration is not None:
                raise InvalidAttendance(
                    "walk-in attendance cannot be reclassified as registered"
                )
            registration = None

        replay_candidate = self._replay_candidate(
            actor=actor,
            action=EventAttendanceTransition.Action.CORRECT,
            idempotency_key=idempotency_key,
        )
        payload = {
            "action": EventAttendanceTransition.Action.CORRECT,
            "attendance_id": attendance_id,
            "event_id": event.event_id,
            "subject_id": attendance.subject_id,
            "origin": attendance.origin,
            "resulting_observed_at": resulting_observed_at.isoformat(),
            "resulting_supporting_registration_id": (
                registration.pk if registration else None
            ),
            "requested_outcome": EventAttendance.Status.PRESENT,
            "prior_transition_id": (
                replay_candidate.previous_transition_id
                if replay_candidate is not None
                else prior.pk
            ),
            "rationale": rationale,
            "evidence": self._evidence_fingerprint(evidence),
        }
        fingerprint = self._fingerprint(payload)
        replay = self._replay(
            actor=actor,
            action=EventAttendanceTransition.Action.CORRECT,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay
        authority_action = (
            "historical_correct_attendance"
            if event.state == Event.State.ARCHIVED
            else "correct_attendance"
        )
        target = self._target(
            actor=actor,
            action=authority_action,
            event=event,
            subject=attendance.subject,
            origin=attendance.origin,
            requested_outcome=EventAttendance.Status.PRESENT,
            registration=registration,
            attendance=attendance,
            prior=prior,
            observed_at=resulting_observed_at,
        )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            actor=actor,
            action=authority_action,
            target=target,
            timestamp=recorded_at,
        )
        transition = EventAttendanceTransition.objects.create(
            attendance=attendance,
            previous_transition=prior,
            sequence=prior.sequence + 1,
            action=EventAttendanceTransition.Action.CORRECT,
            from_status=attendance.status,
            to_status=attendance.status,
            actor=actor,
            authority_reference=authority_reference,
            authority_evaluated_at=recorded_at,
            origin=attendance.origin,
            previous_observed_at=attendance.observed_at,
            resulting_observed_at=resulting_observed_at,
            previous_supporting_registration=locked_registrations.get(
                attendance.supporting_registration_id
            ),
            resulting_supporting_registration=registration,
            basis="bounded_fact_correction",
            rationale=rationale,
            idempotency_key=idempotency_key,
            payload_fingerprint=fingerprint,
            recorded_at=recorded_at,
        )
        self._persist_evidence(
            transition=transition,
            actor=actor,
            evidence=evidence,
            supplied_at=recorded_at,
        )
        EventAttendance.objects.filter(pk=attendance.pk).update(
            observed_at=resulting_observed_at,
            supporting_registration=registration,
            updated_at=recorded_at,
        )
        return transition

    @transaction.atomic
    def void_attendance(
        self,
        *,
        identity: User,
        attendance_id: str,
        rationale: str,
        evidence: Iterable[AttendanceEvidence],
        idempotency_key: str,
    ) -> EventAttendanceTransition:
        """Void a PRESENT attendance atomically and return its
        transition.

        Delegate input checks, locks and authority to _status_transition
        with action VOID and result VOIDED. After the preliminary
        checks, a matching retry returns its stored transition before
        the PRESENT check. A changed fingerprint raises
        AttendanceIdempotencyConflict. A fresh request in the wrong
        state raises InvalidAttendanceTransition.

        On success, append VOID and its evidence and update the
        attendance status and updated_at. The observation and
        registration are kept.
        """
        return self._status_transition(
            identity=identity,
            attendance_id=attendance_id,
            action=EventAttendanceTransition.Action.VOID,
            expected=EventAttendance.Status.PRESENT,
            resulting=EventAttendance.Status.VOIDED,
            rationale=rationale,
            evidence=evidence,
            idempotency_key=idempotency_key,
        )

    @transaction.atomic
    def reinstate_attendance(
        self,
        *,
        identity: User,
        attendance_id: str,
        rationale: str,
        evidence: Iterable[AttendanceEvidence],
        idempotency_key: str,
    ) -> EventAttendanceTransition:
        """Reinstate a VOIDED attendance atomically and return its
        transition.

        Delegate input checks, locks and authority to _status_transition
        with action REINSTATE and result PRESENT. After the preliminary
        checks, a matching retry returns its stored transition before
        the VOIDED check. A changed fingerprint raises
        AttendanceIdempotencyConflict. A fresh request in the wrong
        state raises InvalidAttendanceTransition.

        On success, append REINSTATE and its evidence and update the
        status and updated_at. The attendance keeps its observation and
        registration as they stand at reinstatement.
        """
        return self._status_transition(
            identity=identity,
            attendance_id=attendance_id,
            action=EventAttendanceTransition.Action.REINSTATE,
            expected=EventAttendance.Status.VOIDED,
            resulting=EventAttendance.Status.PRESENT,
            rationale=rationale,
            evidence=evidence,
            idempotency_key=idempotency_key,
        )

    def _status_transition(
        self,
        *,
        identity: User,
        attendance_id: str,
        action: str,
        expected: str,
        resulting: str,
        rationale: str,
        evidence: Iterable[AttendanceEvidence],
        idempotency_key: str,
    ) -> EventAttendanceTransition:
        """Apply VOID or REINSTATE in the caller's transaction.

        Read the aware command clock. Strip attendance ID, rationale and
        key, validate attestation evidence, then resolve the active
        actor. Invalid text, a naive clock or untyped evidence raises
        ValidationError. Empty or wrongly classified evidence raises
        InvalidAttendance. Actor refusal raises AttendanceNotAuthorised.

        Read attendance, Event and subject without a lock. An unknown ID
        raises EventAttendance.DoesNotExist. Lock the Event, then any
        registration named by the snapshot. Require the same Event and
        subject (InvalidAttendance), in any registration state. Lock the
        attendance and its latest transition. No separate subject lock
        is taken.

        Find the prior use of this actor, action and key. Fingerprint
        with its original predecessor on retry, using the locked
        attendance's observed time and registration ID. A mismatch
        raises AttendanceIdempotencyConflict. A match returns the stored
        transition before checking status, capability or actor epoch. On
        a miss, require the expected status or raise
        InvalidAttendanceTransition.

        Ask for void_attendance or reinstate_attendance, prefixed with
        historical_ only on an ARCHIVED Event. Send the stored
        observation time in the target and command time to the
        capability. There is no separate Event-state or actor/subject
        exclusion. evaluate checks the reference, then locks and
        rechecks actor and credential. Denial or access failure raises
        AttendanceNotAuthorised. An awaitable raises TypeError.

        Append the next linked transition with the status change,
        rationale and authority. Use the locked attendance time and the
        snapshot registration for both sides of the transition. Write
        the evidence, then update status and updated_at by queryset.
        Return the transition. Model validation can raise
        ValidationError. An IntegrityError, including one for duplicate
        evidence, propagates.
        """
        recorded_at = self._now()
        attendance_id = self._text(attendance_id, "attendance_id")
        rationale = self._text(rationale, "rationale")
        idempotency_key = self._text(idempotency_key, "idempotency_key")
        evidence = self._evidence_payload(evidence)
        actor = self.authority.resolve_actor(identity)
        snapshot = EventAttendance.objects.select_related("event", "subject").get(
            attendance_id=attendance_id
        )
        event = Event.objects.select_for_update().get(pk=snapshot.event_id)
        registration = self._lock_registration(
            snapshot.supporting_registration,
            event=event,
            subject=snapshot.subject,
            require_active=False,
        )
        attendance = EventAttendance.objects.select_for_update().get(pk=snapshot.pk)
        prior = attendance.transitions.select_for_update().order_by(
            "sequence"
        ).last()
        replay_candidate = self._replay_candidate(
            actor=actor,
            action=action,
            idempotency_key=idempotency_key,
        )
        payload = {
            "action": action,
            "attendance_id": attendance_id,
            "event_id": event.event_id,
            "subject_id": attendance.subject_id,
            "origin": attendance.origin,
            "observed_at": attendance.observed_at.isoformat(),
            "supporting_registration_id": attendance.supporting_registration_id,
            "requested_outcome": resulting,
            "prior_transition_id": (
                replay_candidate.previous_transition_id
                if replay_candidate is not None
                else prior.pk
            ),
            "rationale": rationale,
            "evidence": self._evidence_fingerprint(evidence),
        }
        fingerprint = self._fingerprint(payload)
        replay = self._replay(
            actor=actor,
            action=action,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        if replay is not None:
            return replay
        if attendance.status != expected:
            raise InvalidAttendanceTransition(
                f"cannot {action} attendance from {attendance.status}"
            )
        authority_action = (
            f"historical_{action}_attendance"
            if event.state == Event.State.ARCHIVED
            else f"{action}_attendance"
        )
        target = self._target(
            actor=actor,
            action=authority_action,
            event=event,
            subject=attendance.subject,
            origin=attendance.origin,
            requested_outcome=resulting,
            registration=registration,
            attendance=attendance,
            prior=prior,
            observed_at=attendance.observed_at,
        )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            actor=actor,
            action=authority_action,
            target=target,
            timestamp=recorded_at,
        )
        transition = EventAttendanceTransition.objects.create(
            attendance=attendance,
            previous_transition=prior,
            sequence=prior.sequence + 1,
            action=action,
            from_status=attendance.status,
            to_status=resulting,
            actor=actor,
            authority_reference=authority_reference,
            authority_evaluated_at=recorded_at,
            origin=attendance.origin,
            previous_observed_at=attendance.observed_at,
            resulting_observed_at=attendance.observed_at,
            previous_supporting_registration=registration,
            resulting_supporting_registration=registration,
            basis=(
                "authorised_assertion_void"
                if action == EventAttendanceTransition.Action.VOID
                else "authorised_assertion_reinstatement"
            ),
            rationale=rationale,
            idempotency_key=idempotency_key,
            payload_fingerprint=fingerprint,
            recorded_at=recorded_at,
        )
        self._persist_evidence(
            transition=transition,
            actor=actor,
            evidence=evidence,
            supplied_at=recorded_at,
        )
        EventAttendance.objects.filter(pk=attendance.pk).update(
            status=resulting,
            updated_at=recorded_at,
        )
        return transition


__all__ = [
    "AttendanceEvidence",
    "AttendanceIdempotencyConflict",
    "AttendanceNotAuthorised",
    "DuplicateEventAttendance",
    "EventAttendanceAuthority",
    "EventAttendanceAuthorityTarget",
    "EventAttendanceCapability",
    "EventAttendanceError",
    "EventAttendanceService",
    "InvalidAttendance",
    "InvalidAttendanceTransition",
    "WalkInEligibility",
]