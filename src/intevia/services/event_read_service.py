"""EVENT-owned default-deny read projections for the personal Event surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.db.models import Prefetch, Q, QuerySet

from core.models import (
    Event,
    EventAttendance,
    EventAttendanceTransition,
    EventRegistration,
    EventRegistrationTransition,
    Identity,
)


class EventNotVisible(LookupError):
    """Raised without revealing whether an Event or registration exists."""


def visible_event_queryset(identity: Identity) -> QuerySet[Event]:
    """Return Events visible through the accepted EVENT relationships."""
    return Event.objects.filter(
        Q(owner=identity)
        | Q(registrations__participant=identity)
        | Q(participations__participant=identity)
        | Q(attendance_records__subject=identity)
    ).distinct()


REGISTRATION_CURRENT = "A current registration record exists for you."
REGISTRATION_NONE = "No registration record is shown for you for this Event."
REGISTRATION_CANCELLED = (
    "A previous registration record was cancelled. "
    "No current registration record is shown."
)
ATTENDANCE_PRESENT = "A current attendance record says you were present."
ATTENDANCE_WITHDRAWN = (
    "A previous attendance record was withdrawn. "
    "It does not record you as absent."
)
ATTENDANCE_NONE = (
    "No attendance record is currently held for you for this Event. "
    "This is not an absence record."
)


@dataclass(frozen=True, slots=True)
class EventSummary:
    event_id: str
    title: str
    state: str


@dataclass(frozen=True, slots=True)
class RegistrationCurrentSummary:
    message: str
    history_available: bool


@dataclass(frozen=True, slots=True)
class AttendanceCompactSummary:
    message: str
    history_available: bool


@dataclass(frozen=True, slots=True)
class EventHomeSummary:
    event_id: str
    title: str
    description: str
    registration: RegistrationCurrentSummary
    attendance: AttendanceCompactSummary | None


@dataclass(frozen=True, slots=True)
class PersonalHome:
    display_name: str
    events: tuple[EventHomeSummary, ...]


@dataclass(frozen=True, slots=True)
class EventDetailPresentation:
    event_id: str
    title: str
    description: str
    registration: RegistrationCurrentSummary
    attendance: AttendanceCompactSummary


@dataclass(frozen=True, slots=True)
class RecordHistoryEntry:
    message: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RecordHistory:
    event_id: str
    event_title: str
    heading: str
    entries: tuple[RecordHistoryEntry, ...]


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
            current = Identity.objects.get(
                pk=identity.pk,
                identity_id=identity.identity_id,
                credential_id=identity.credential_id,
            )
        except Identity.DoesNotExist as exc:
            raise EventNotVisible("resource is not visible") from exc
        if current.access_state != Identity.AccessState.ACTIVE:
            raise EventNotVisible("resource is not visible")
        return current

    @staticmethod
    def _registration_summary(
        registrations: list[EventRegistration],
    ) -> RegistrationCurrentSummary:
        if not registrations:
            return RegistrationCurrentSummary(REGISTRATION_NONE, False)

        by_id = {registration.pk: registration for registration in registrations}
        if len(by_id) != len(registrations):
            raise EventNotVisible("resource is not visible")
        successors: dict[int, list[EventRegistration]] = {
            registration.pk: [] for registration in registrations
        }
        roots = []
        for registration in registrations:
            if registration.predecessor_id is None:
                roots.append(registration)
                continue
            predecessor = by_id.get(registration.predecessor_id)
            if (
                predecessor is None
                or predecessor.event_id != registration.event_id
                or predecessor.participant_id != registration.participant_id
                or predecessor.state != EventRegistration.State.CANCELLED
            ):
                raise EventNotVisible("resource is not visible")
            successors[predecessor.pk].append(registration)
        if len(roots) != 1 or any(len(items) > 1 for items in successors.values()):
            raise EventNotVisible("resource is not visible")

        visited = set()
        current = roots[0]
        while current is not None:
            if current.pk in visited:
                raise EventNotVisible("resource is not visible")
            visited.add(current.pk)
            next_items = successors[current.pk]
            current = next_items[0] if next_items else None
        if len(visited) != len(registrations):
            raise EventNotVisible("resource is not visible")

        registered = [
            registration
            for registration in registrations
            if registration.state == EventRegistration.State.REGISTERED
        ]
        if len(registered) > 1:
            raise EventNotVisible("resource is not visible")
        terminal = next(item for item in registrations if not successors[item.pk])
        if registered:
            if registered[0] != terminal:
                raise EventNotVisible("resource is not visible")
            return RegistrationCurrentSummary(REGISTRATION_CURRENT, True)
        if terminal.state != EventRegistration.State.CANCELLED:
            raise EventNotVisible("resource is not visible")
        return RegistrationCurrentSummary(REGISTRATION_CANCELLED, True)

    @staticmethod
    def _attendance_summary(
        attendance: EventAttendance | None,
    ) -> AttendanceCompactSummary:
        if attendance is None:
            return AttendanceCompactSummary(ATTENDANCE_NONE, False)
        if attendance.status == EventAttendance.Status.PRESENT:
            return AttendanceCompactSummary(ATTENDANCE_PRESENT, True)
        if attendance.status == EventAttendance.Status.VOIDED:
            return AttendanceCompactSummary(ATTENDANCE_WITHDRAWN, True)
        raise EventNotVisible("resource is not visible")

    @staticmethod
    def _personal_events(identity: Identity) -> QuerySet[Event]:
        registrations = EventRegistration.objects.filter(
            participant=identity
        ).order_by("registered_at", "pk")
        attendances = EventAttendance.objects.filter(subject=identity)
        return visible_event_queryset(identity).prefetch_related(
            Prefetch(
                "registrations",
                queryset=registrations,
                to_attr="personal_registrations",
            ),
            Prefetch(
                "attendance_records",
                queryset=attendances,
                to_attr="personal_attendances",
            ),
        )

    @classmethod
    def _present_event(cls, event: Event) -> EventDetailPresentation:
        attendances = event.personal_attendances
        if len(attendances) > 1:
            raise EventNotVisible("resource is not visible")
        attendance = attendances[0] if attendances else None
        return EventDetailPresentation(
            event_id=event.event_id,
            title=event.title,
            description=event.description,
            registration=cls._registration_summary(event.personal_registrations),
            attendance=cls._attendance_summary(attendance),
        )

    @classmethod
    def home(cls, identity: Identity) -> PersonalHome:
        identity = cls._active(identity)
        events = cls._personal_events(identity).order_by("created_at", "pk")
        summaries = []
        for event in events:
            presented = cls._present_event(event)
            summaries.append(
                EventHomeSummary(
                    event_id=presented.event_id,
                    title=presented.title,
                    description=presented.description,
                    registration=presented.registration,
                    attendance=(
                        presented.attendance
                        if presented.attendance.history_available
                        else None
                    ),
                )
            )
        return PersonalHome(identity.display_name, tuple(summaries))

    @classmethod
    def present_event(
        cls, identity: Identity, event_id: str
    ) -> EventDetailPresentation:
        identity = cls._active(identity)
        try:
            event = cls._personal_events(identity).get(event_id=event_id)
        except (Event.DoesNotExist, Event.MultipleObjectsReturned) as exc:
            raise EventNotVisible("resource is not visible") from exc
        return cls._present_event(event)

    @classmethod
    def registration_history(
        cls,
        identity: Identity,
        event_id: str,
        registration_id: str | None = None,
    ) -> RecordHistory:
        identity = cls._active(identity)
        try:
            event = visible_event_queryset(identity).get(event_id=event_id)
        except (Event.DoesNotExist, Event.MultipleObjectsReturned) as exc:
            raise EventNotVisible("resource is not visible") from exc
        registrations = list(
            EventRegistration.objects.filter(event=event, participant=identity)
            .prefetch_related(
                Prefetch(
                    "transitions",
                    queryset=EventRegistrationTransition.objects.order_by(
                        "occurred_at", "pk"
                    ),
                    to_attr="presentation_transitions",
                )
            )
            .order_by("registered_at", "pk")
        )
        summary = cls._registration_summary(registrations)
        selected = registrations
        if registration_id is not None:
            selected = [
                registration
                for registration in registrations
                if registration.registration_id == registration_id
            ]
            if len(selected) != 1:
                raise EventNotVisible("resource is not visible")
        if not summary.history_available:
            return RecordHistory(
                event.event_id,
                event.title,
                "Registration record history",
                (),
            )
        messages = {
            "register_self": "A registration record was created.",
            "register_third_party": "A registration record was created.",
            "cancel": "The registration record was cancelled.",
            "re_register": "A registration record was created after an earlier cancellation.",
        }
        entries = []
        for registration in selected:
            for transition in registration.presentation_transitions:
                message = messages.get(transition.action_type)
                if message is None:
                    raise EventNotVisible("resource is not visible")
                entries.append(RecordHistoryEntry(message, transition.occurred_at))
        entries.sort(key=lambda entry: entry.occurred_at)
        return RecordHistory(
            event.event_id,
            event.title,
            (
                "Registration record"
                if registration_id is not None
                else "Registration record history"
            ),
            tuple(entries),
        )

    @classmethod
    def attendance_history(
        cls, identity: Identity, event_id: str
    ) -> RecordHistory:
        identity = cls._active(identity)
        try:
            event = visible_event_queryset(identity).get(event_id=event_id)
        except (Event.DoesNotExist, Event.MultipleObjectsReturned) as exc:
            raise EventNotVisible("resource is not visible") from exc
        attendance = (
            EventAttendance.objects.filter(event=event, subject=identity)
            .prefetch_related(
                Prefetch(
                    "transitions",
                    queryset=EventAttendanceTransition.objects.order_by("sequence"),
                    to_attr="presentation_transitions",
                )
            )
            .first()
        )
        if attendance is None:
            return RecordHistory(event.event_id, event.title, "Attendance record history", ())
        messages = {
            "record": "An attendance record was created saying you were present.",
            "correct": "The attendance record was corrected.",
            "void": "The attendance record was withdrawn.",
            "reinstate": "The attendance record was restored as a present record.",
        }
        entries = []
        for transition in attendance.presentation_transitions:
            message = messages.get(transition.action)
            if message is None:
                raise EventNotVisible("resource is not visible")
            entries.append(RecordHistoryEntry(message, transition.recorded_at))
        return RecordHistory(
            event.event_id,
            event.title,
            "Attendance record history",
            tuple(entries),
        )

    @classmethod
    @transaction.atomic
    def list_visible(cls, identity: Identity) -> tuple[EventSummary, ...]:
        identity = cls._active(identity)
        events = visible_event_queryset(identity).order_by("created_at", "pk")
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
            event = visible_event_queryset(identity).get(event_id=event_id)
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
            event = visible_event_queryset(identity).get(event_id=event_id)
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
    "AttendanceCompactSummary",
    "AttendanceInspection",
    "AttendanceSummary",
    "EventDetailPresentation",
    "EventHomeSummary",
    "EventNotVisible",
    "EventReadService",
    "PersonalHome",
    "RecordHistory",
    "RecordHistoryEntry",
    "RegistrationCurrentSummary",
    "visible_event_queryset",
]