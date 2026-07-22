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
from django.utils import timezone

from core.models import (
    Event,
    EventAttendance,
    EventAttendanceEligibilityReceipt,
    EventAttendanceEvidenceReference,
    EventAttendanceTransition,
    EventRegistration,
    Identity,
)


class EventAttendanceError(Exception):
    """Base failure for governed Event attendance."""


class AttendanceNotAuthorised(EventAttendanceError):
    pass


class InvalidAttendance(EventAttendanceError):
    pass


class DuplicateEventAttendance(EventAttendanceError):
    pass


class AttendanceIdempotencyConflict(EventAttendanceError):
    pass


class InvalidAttendanceTransition(EventAttendanceError):
    pass


@dataclass(frozen=True, slots=True)
class AttendanceEvidence:
    reference: str
    evidence_type: str
    provenance: str
    classification: str = (
        EventAttendanceEvidenceReference.Classification.ACTOR_ATTESTATION
    )


@dataclass(frozen=True, slots=True)
class WalkInEligibility:
    basis_reference: str
    basis_type: str = (
        EventAttendanceEligibilityReceipt.BasisType.GOVERNED_WALK_IN
    )


@dataclass(frozen=True, slots=True)
class EventAttendanceAuthorityTarget:
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
    def authorise(
        self,
        *,
        identity: Identity,
        action: str,
        target: EventAttendanceAuthorityTarget,
        timestamp: datetime,
    ) -> str | None: ...


class EventAttendanceAuthority:
    """Attendance-specific local capability evaluation and actor recheck."""

    def __init__(self, capability: EventAttendanceCapability) -> None:
        if not callable(getattr(capability, "authorise", None)):
            raise TypeError("capability must provide authorise")
        if inspect.iscoroutinefunction(capability.authorise):
            raise TypeError("attendance capability must be local and synchronous")
        self.capability = capability

    @staticmethod
    def resolve_actor(identity: User) -> Identity:
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
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        if not isinstance(authority, EventAttendanceAuthority):
            raise TypeError("authority must be EventAttendanceAuthority")
        self.authority = authority
        self.clock = clock

    @staticmethod
    def _text(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{name} is required")
        return value.strip()

    @staticmethod
    def _at(value: datetime, name: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError(f"{name} must be timezone-aware")
        return value

    def _now(self) -> datetime:
        return self._at(self.clock(), "server observation time")

    @staticmethod
    def _fingerprint(payload: dict[str, object]) -> str:
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