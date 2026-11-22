"""EVENT-owned default-deny read projections for the personal Event surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

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
from src.intevia.services.clock import database_now

if TYPE_CHECKING:
    from src.intevia.services.event_resource_relationship_read_service import (
        EventResourcePresentation,
        EventResourceRelationshipReadService,
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
    """One visible Event as listed: its id, title and stored state."""
    event_id: str
    title: str
    state: str


@dataclass(frozen=True, slots=True)
class RegistrationCurrentSummary:
    """The viewer's registration on an Event, as a fixed message.

    message is REGISTRATION_CURRENT, REGISTRATION_CANCELLED or
    REGISTRATION_NONE. history_available is False only when the
    viewer holds no registration for the Event.
    """
    message: str
    history_available: bool


@dataclass(frozen=True, slots=True)
class AttendanceCompactSummary:
    """The viewer's attendance on an Event, as a fixed message.

    message is ATTENDANCE_PRESENT, ATTENDANCE_WITHDRAWN (a voided
    record) or ATTENDANCE_NONE, which is not a record of absence.
    history_available is False only when no record is held.
    """
    message: str
    history_available: bool


@dataclass(frozen=True, slots=True)
class EventHomeSummary:
    """One Event on the viewer's home page.

    attendance is None when no attendance record is held for the
    viewer; registration is always given.
    """
    event_id: str
    title: str
    description: str
    registration: RegistrationCurrentSummary
    attendance: AttendanceCompactSummary | None


@dataclass(frozen=True, slots=True)
class PersonalHome:
    """The viewer's display name and the Events on their home page."""
    display_name: str
    events: tuple[EventHomeSummary, ...]


@dataclass(frozen=True, slots=True)
class EventDetailPresentation:
    """The viewer's page for one Event, with their records as messages.

    registration and attendance are the viewer's own summaries.
    resources is () unless present_event was given a resource reader.
    """
    event_id: str
    title: str
    description: str
    registration: RegistrationCurrentSummary
    attendance: AttendanceCompactSummary
    resources: tuple["EventResourcePresentation", ...] = ()


@dataclass(frozen=True, slots=True)
class RecordHistoryEntry:
    """One history line: a fixed message and its time.

    The time is a registration transition's occurred_at or an
    attendance transition's recorded_at. No actor, authority or
    reference is carried.
    """
    message: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RecordHistory:
    """The viewer's history of one kind of record on one Event.

    heading names what is shown; entries may be empty.
    """
    event_id: str
    event_title: str
    heading: str
    entries: tuple[RecordHistoryEntry, ...]


@dataclass(frozen=True, slots=True)
class RegistrationSummary:
    """One of the viewer's registrations, with its stored codes.

    state and origin are the stored values, not display text.
    """
    registration_id: str
    state: str
    origin: str
    registered_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    """A registration evidence reference's type and time only.

    The reference itself and who supplied it are not carried.
    """
    reference_type: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RegistrationTransitionSummary:
    """One registration transition, its stored codes and evidence.

    The actor, authority, idempotency key and any cancellation
    basis are not carried.
    """
    action_type: str
    from_state: str
    to_state: str
    occurred_at: datetime
    evidence: tuple[EvidenceSummary, ...]


@dataclass(frozen=True, slots=True)
class RegistrationInspection:
    """One of the viewer's registrations with its transitions.

    eligibility_basis_type is carried; the basis and policy
    references and the predecessor are not.
    """
    registration_id: str
    state: str
    origin: str
    event_state_at_registration: str
    eligibility_basis_type: str
    registered_at: datetime
    transitions: tuple[RegistrationTransitionSummary, ...]


@dataclass(frozen=True, slots=True)
class AttendanceSummary:
    """The viewer's attendance on an Event, with its stored codes.

    status is "unrecorded", and the other fields None, when no
    attendance record is held; otherwise the stored status.
    """
    status: str
    attendance_id: str | None
    origin: str | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class AttendanceEvidenceSummary:
    """An attendance evidence reference's type, class and time.

    The reference itself, its provenance and its supplier are not
    carried.
    """
    evidence_type: str
    classification: str
    supplied_at: datetime


@dataclass(frozen=True, slots=True)
class AttendanceTransitionSummary:
    """One attendance transition, its stored codes and evidence.

    The actor, authority, basis, rationale, and the observed times
    and registrations before and after, are not carried.
    """
    sequence: int
    action: str
    from_status: str
    to_status: str
    recorded_at: datetime
    evidence: tuple[AttendanceEvidenceSummary, ...]


@dataclass(frozen=True, slots=True)
class AttendanceInspection:
    """The viewer's attendance on one Event, with its transitions.

    Without a record, event_id is retained, status is "unrecorded"
    and transitions is empty. attendance_id, origin and observed_at
    are None.
    """
    event_id: str
    status: str
    attendance_id: str | None
    origin: str | None
    observed_at: datetime | None
    transitions: tuple[AttendanceTransitionSummary, ...]


@dataclass(frozen=True, slots=True)
class EventInspection:
    """One visible Event with the viewer's records as stored.

    registrations holds only the viewer's own registrations.
    """
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
        """Return the current ACTIVE Identity for the supplied viewer.

        Read by primary key, identity ID and credential ID. A missing
        match or a non-ACTIVE stored state raises EventNotVisible.
        This reads no credential row and does not compare access epochs.
        It starts no transaction and takes no explicit row lock.
        """
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
        """Summarise one viewer's registrations for one Event.

        The callers pass one participant's records for one Event. An
        empty list gives REGISTRATION_NONE, without history. Otherwise
        the records must form one chain: no repeated row; exactly one
        without a predecessor; every other row's predecessor in the
        list, CANCELLED, and for the same Event and participant; at most
        one successor each; every row reached from the first. At most
        one row may be REGISTERED, and it must be the last; otherwise
        the last must be CANCELLED.

        Gives REGISTRATION_CURRENT or REGISTRATION_CANCELLED, with
        history. Any break raises EventNotVisible, which hides the
        Event rather than reporting the fault. No database read.
        """
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
        """Summarise the viewer's attendance record, if any.

        None gives ATTENDANCE_NONE, without history. PRESENT gives
        ATTENDANCE_PRESENT and VOIDED gives ATTENDANCE_WITHDRAWN, both
        with history. Any other status raises EventNotVisible.
        """
        if attendance is None:
            return AttendanceCompactSummary(ATTENDANCE_NONE, False)
        if attendance.status == EventAttendance.Status.PRESENT:
            return AttendanceCompactSummary(ATTENDANCE_PRESENT, True)
        if attendance.status == EventAttendance.Status.VOIDED:
            return AttendanceCompactSummary(ATTENDANCE_WITHDRAWN, True)
        raise EventNotVisible("resource is not visible")

    @staticmethod
    def _personal_events(identity: Identity) -> QuerySet[Event]:
        """Return the visible Events with the viewer's own records.

        Each Event carries personal_registrations (the viewer's, by
        registered_at, then primary key) and personal_attendances (the
        viewer's). The queryset is lazy and is not ordered here.
        """
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
        """Build the viewer's page from one _personal_events row.

        Raises EventNotVisible if more than one attendance is held, or
        if either summary refuses. resources is left empty.
        """
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
        """Return PersonalHome for the viewer, in Event creation order.

        Refresh the viewer with _active, then read visible Events in all
        states by created_at and primary key. Prefetch the viewer's own
        registrations and attendances.
        Registration is always a message, including when none is held.
        Attendance is a message only when a record is held; else None.

        An _active refusal, duplicate attendance, invalid registration
        chain or unknown attendance status raises EventNotVisible for
        the whole home. No transaction or row lock is requested here.
        """
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
        cls,
        identity: Identity,
        event_id: str,
        *,
        resource_reader: "EventResourceRelationshipReadService | None" = None,
        evaluated_at: datetime | None = None,
    ) -> EventDetailPresentation:
        """Return the viewer's presentation of one visible Event.

        Refresh the viewer with _active. Fetch exactly one visible Event
        with the viewer's registrations and attendance. An _active
        refusal, zero or multiple Event matches, duplicate attendance,
        a broken registration chain or an unknown attendance status
        raises EventNotVisible.

        Without a resource reader, resources is () and time is unused.
        Otherwise pass the refreshed viewer, Event and supplied time
        (default database_now(), the INTEVIA Clock on the default
        database) to its present method. It may return ().
        Errors escaping the reader propagate. This method starts no
        transaction or row lock of its own. The resource reader can lock
        the viewer in its own atomic block.
        """
        identity = cls._active(identity)
        try:
            event = cls._personal_events(identity).get(event_id=event_id)
        except (Event.DoesNotExist, Event.MultipleObjectsReturned) as exc:
            raise EventNotVisible("resource is not visible") from exc
        presented = cls._present_event(event)
        if resource_reader is None:
            return presented
        resources = resource_reader.present(
            viewer=identity,
            event=event,
            evaluated_at=evaluated_at or database_now(),
        )
        return EventDetailPresentation(
            event_id=presented.event_id,
            title=presented.title,
            description=presented.description,
            registration=presented.registration,
            attendance=presented.attendance,
            resources=resources,
        )

    @classmethod
    def registration_history(
        cls,
        identity: Identity,
        event_id: str,
        registration_id: str | None = None,
    ) -> RecordHistory:
        """Return the viewer's registration history for one Event.

        Refresh with _active and require exactly one visible Event.
        Read all the viewer's registrations there and prefetch their
        transitions. Check the whole chain before selecting all rows or
        the one matching registration_id. An _active refusal, zero or
        multiple Event matches, a broken chain or an ID matching other
        than one personal registration raises EventNotVisible.

        Map self and third-party registration to the same creation
        message. Cancellation and re-registration each have their own.
        Unknown selected actions raise EventNotVisible. Order entries
        by occurred_at.
        No registrations and no registration_id give an empty history,
        not a refusal. With a registration_id that call is refused.
        Use "Registration record" when an ID was supplied and
        "Registration record history" otherwise. No transaction or row
        lock is requested here.
        """
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
        """Return the viewer's attendance history for one Event.

        Refresh with _active and require exactly one visible Event.
        An _active refusal or zero or multiple Event matches raises
        EventNotVisible. Read the first attendance for this subject and
        Event, with transitions in sequence order. No record gives an
        empty history. The stored attendance status is not checked.

        Map record, correct, void and reinstate to fixed messages at
        each transition's recorded_at. An unknown action raises
        EventNotVisible. No transaction or row lock is requested here.
        """
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
        """List visible Events by created_at and primary key.

        In an atomic block, refresh the viewer with _active, which may
        raise EventNotVisible. Query distinct Events through ownership,
        registration, participation or attendance in any Event state.
        Return a tuple of IDs, titles and states as EventSummary values.
        No personal record is materialised or validated. No visible
        Events gives an empty tuple. No explicit row lock is taken.
        """
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
        """Return one visible Event with the viewer's stored records.

        In an atomic block, refresh with _active and fetch exactly one
        visible Event. An _active refusal or zero or multiple matches
        raises EventNotVisible. Read the first personal attendance and
        the viewer's registrations ordered by registered_at, then pk.
        Return EventInspection with stored codes and "unrecorded" for
        missing attendance. No registration chain or attendance status
        is validated and no explicit row lock is taken.
        """
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
        """Inspect the viewer's own registration and its transitions.

        In an atomic block, refresh with _active. Fetch the registration
        by registration ID, Event ID and participant, joining the Event
        and prefetching transitions and evidence. An _active refusal or
        a missing registration raises EventNotVisible. Only the missing
        row error is translated by the registration lookup.

        Return RegistrationInspection with stored codes. Order both
        transitions and their evidence by occurred_at, then primary key.
        The chain and action codes are unchecked. No row lock is taken.
        """
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
        """Inspect the viewer's attendance and its transitions.

        In an atomic block, refresh with _active and fetch exactly one
        visible Event. An _active refusal or zero or multiple matches
        raises EventNotVisible. Read the first attendance for the viewer
        and Event, prefetching transitions and evidence. If absent,
        retain the Event ID with status "unrecorded" and no transitions.

        Otherwise return AttendanceInspection with the stored status,
        ID, origin and observed time. Read transitions in sequence order
        and their evidence by supplied_at, then primary key. Include its
        type, classification and supply time. Status and action codes
        are unchecked. No explicit row lock is taken.
        """
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