"""EVENT-owned default-deny read projections for the S007 product shell."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.db.models import Q

from core.models import Event, EventAttendance, EventRegistration, Identity


class EventNotVisible(LookupError):
    """Raised without revealing whether an Event or registration exists."""


@dataclass(frozen=True, slots=True)
class EventSummary:
    event_id: str
    title: str
    state: str


@dataclass(frozen=True, slots=True)
class RegistrationSummary:
    registration_id: str
    state: str
    origin: str
    registered_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    reference_type: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RegistrationTransitionSummary:
    action_type: str
    from_state: str
    to_state: str
    occurred_at: datetime
    evidence: tuple[EvidenceSummary, ...]


@dataclass(frozen=True, slots=True)
class RegistrationInspection:
    registration_id: str
    state: str
    origin: str
    event_state_at_registration: str
    eligibility_basis_type: str
    registered_at: datetime
    transitions: tuple[RegistrationTransitionSummary, ...]


@dataclass(frozen=True, slots=True)
class AttendanceSummary:
    status: str
    attendance_id: str | None
    origin: str | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class AttendanceEvidenceSummary:
    evidence_type: str
    classification: str
    supplied_at: datetime


@dataclass(frozen=True, slots=True)
class AttendanceTransitionSummary:
    sequence: int
    action: str
    from_status: str
    to_status: str
    recorded_at: datetime
    evidence: tuple[AttendanceEvidenceSummary, ...]


@dataclass(frozen=True, slots=True)
class AttendanceInspection:
    event_id: str
    status: str
    attendance_id: str | None
    origin: str | None
    observed_at: datetime | None
    transitions: tuple[AttendanceTransitionSummary, ...]


@dataclass(frozen=True, slots=True)
class EventInspection:
    event_id: str
    title: str
    description: str
    state: str
    registrations: tuple[RegistrationSummary, ...]
    attendance: AttendanceSummary


class EventReadService:
    """Resolve visibility from EVENT relationships, never authentication alone."""

    @staticmethod
    def _active(identity: Identity) -> Identity:
        try:
            current = Identity.objects.select_for_update().get(
                pk=identity.pk,
                identity_id=identity.identity_id,
                credential_id=identity.credential_id,
            )
        except Identity.DoesNotExist as exc:
            raise EventNotVisible("resource is not visible") from exc
        if current.access_state != Identity.AccessState.ACTIVE:
            raise EventNotVisible("resource is not visible")
        return current

    @classmethod
    @transaction.atomic
    def list_visible(cls, identity: Identity) -> tuple[EventSummary, ...]:
        identity = cls._active(identity)
        events = Event.objects.filter(
            Q(owner=identity)
            | Q(registrations__participant=identity)
            | Q(participations__participant=identity)
            | Q(attendance_records__subject=identity)
        ).distinct().order_by("created_at", "pk")
        return tuple(
            EventSummary(event.event_id, event.title, event.state)
            for event in events
        )

    @classmethod
    @transaction.atomic
    def inspect_event(
        cls, identity: Identity, event_id: str
    ) -> EventInspection:
        identity = cls._active(identity)
        try:
            event = Event.objects.filter(
                Q(owner=identity)
                | Q(registrations__participant=identity)
                | Q(participations__participant=identity)
                | Q(attendance_records__subject=identity),
                event_id=event_id,
            ).distinct().get()
        except (Event.DoesNotExist, Event.MultipleObjectsReturned) as exc:
            raise EventNotVisible("resource is not visible") from exc
        registrations = event.registrations.filter(
            participant=identity
        ).order_by("registered_at", "pk")
        attendance = event.attendance_records.filter(subject=identity).first()
        return EventInspection(
            event_id=event.event_id,
            title=event.title,
            description=event.description,
            state=event.state,
            registrations=tuple(
                RegistrationSummary(
                    registration.registration_id,
                    registration.state,
                    registration.origin,
                    registration.registered_at,
                )
                for registration in registrations
            ),
            attendance=AttendanceSummary(
                status=(
                    attendance.status if attendance is not None else "unrecorded"
                ),
                attendance_id=(
                    attendance.attendance_id if attendance is not None else None
                ),
                origin=attendance.origin if attendance is not None else None,
                observed_at=(
                    attendance.observed_at if attendance is not None else None
                ),
            ),
        )

    @classmethod
    @transaction.atomic
    def inspect_registration(
        cls,
        identity: Identity,
        event_id: str,
        registration_id: str,
    ) -> RegistrationInspection:
        identity = cls._active(identity)
        try:
            registration = EventRegistration.objects.select_related(
                "event"
            ).prefetch_related(
                "transitions__evidence_references"
            ).get(
                registration_id=registration_id,
                event__event_id=event_id,
                participant=identity,
            )
        except EventRegistration.DoesNotExist as exc:
            raise EventNotVisible("resource is not visible") from exc
        transitions = []
        for transition in registration.transitions.order_by("occurred_at", "pk"):
            evidence = tuple(
                EvidenceSummary(reference.reference_type, reference.occurred_at)
                for reference in transition.evidence_references.order_by(
                    "occurred_at", "pk"
                )
            )
            transitions.append(
                RegistrationTransitionSummary(
                    action_type=transition.action_type,
                    from_state=transition.from_state,
                    to_state=transition.to_state,
                    occurred_at=transition.occurred_at,
                    evidence=evidence,
                )
            )
        return RegistrationInspection(
            registration_id=registration.registration_id,
            state=registration.state,
            origin=registration.origin,
            event_state_at_registration=registration.event_state_at_registration,
            eligibility_basis_type=registration.eligibility_basis_type,
            registered_at=registration.registered_at,
            transitions=tuple(transitions),
        )

    @classmethod
    @transaction.atomic
    def inspect_attendance(
        cls,
        identity: Identity,
        event_id: str,
    ) -> AttendanceInspection:
        identity = cls._active(identity)
        try:
            event = Event.objects.filter(
                Q(owner=identity)
                | Q(registrations__participant=identity)
                | Q(participations__participant=identity)
                | Q(attendance_records__subject=identity),
                event_id=event_id,
            ).distinct().get()
        except (Event.DoesNotExist, Event.MultipleObjectsReturned) as exc:
            raise EventNotVisible("resource is not visible") from exc
        attendance = (
            EventAttendance.objects.prefetch_related(
                "transitions__evidence_references"
            )
            .filter(event=event, subject=identity)
            .first()
        )
        if attendance is None:
            return AttendanceInspection(
                event_id=event.event_id,
                status="unrecorded",
                attendance_id=None,
                origin=None,
                observed_at=None,
                transitions=(),
            )
        transitions = []
        for transition in attendance.transitions.order_by("sequence"):
            evidence = tuple(
                AttendanceEvidenceSummary(
                    evidence_type=reference.evidence_type,
                    classification=reference.classification,
                    supplied_at=reference.supplied_at,
                )
                for reference in transition.evidence_references.order_by(
                    "supplied_at", "pk"
                )
            )
            transitions.append(
                AttendanceTransitionSummary(
                    sequence=transition.sequence,
                    action=transition.action,
                    from_status=transition.from_status,
                    to_status=transition.to_status,
                    recorded_at=transition.recorded_at,
                    evidence=evidence,
                )
            )
        return AttendanceInspection(
            event_id=event.event_id,
            status=attendance.status,
            attendance_id=attendance.attendance_id,
            origin=attendance.origin,
            observed_at=attendance.observed_at,
            transitions=tuple(transitions),
        )


__all__ = [
    "AttendanceInspection",
    "AttendanceSummary",
    "EventNotVisible",
    "EventReadService",
]