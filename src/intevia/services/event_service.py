"""Transactional orchestration for the governed Event lifecycle."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import (
    Event,
    EventEvidenceReference,
    EventParticipation,
    EventTransition,
    Identity,
)
from src.intevia.services.contribution_authority import ContributionAuthority


class EventServiceError(Exception):
    """Base failure for Event orchestration."""


class InvalidEventTransition(EventServiceError):
    """Raised when an Event command is invalid for its current state."""


class EventParticipationWritesRetired(EventServiceError):
    """Raised when application code attempts a legacy participation write."""


class EventService:
    """Persist authority-gated Event changes and lineage atomically."""

    _TRANSITIONS = {
        Event.State.DRAFT: ("publish_event", Event.State.PUBLISHED),
        Event.State.PUBLISHED: ("activate_event", Event.State.ACTIVE),
        Event.State.ACTIVE: ("complete_event", Event.State.COMPLETED),
        Event.State.COMPLETED: ("archive_event", Event.State.ARCHIVED),
    }

    def __init__(self, *, authority: ContributionAuthority) -> None:
        """Keep the authority. It must be a ContributionAuthority
        (isinstance; TypeError otherwise); its capability is not checked."""
        if not isinstance(authority, ContributionAuthority):
            raise TypeError("authority must preserve the existing capability contract")
        self.authority = authority

    @staticmethod
    def _at(value: datetime | None) -> datetime:
        """Use the supplied time, or timezone.now() when the value is falsy.

        Require tzinfo and a non-None UTC offset, else raise Django's
        ValidationError. No explicit datetime type check is made; attribute
        or time-zone errors propagate. Return the value without converting
        to UTC or comparing it with earlier Event times.
        """
        value = value or timezone.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("timestamp must be timezone-aware")
        return value

    @staticmethod
    def _text(value: str, name: str) -> str:
        """Return the text stripped; a non-str or blank value raises Django's
        ValidationError naming the field. No length or format is checked."""
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{name} is required")
        return value.strip()

    @staticmethod
    def _locked(event_id: str) -> Event:
        """Lock and return the Event with this event_id (select_for_update).

        A missing Event raises Event.DoesNotExist, unconverted. The caller
        holds the transaction.
        """
        return Event.objects.select_for_update().get(event_id=event_id)

    @staticmethod
    def _record_transition(
        *,
        event: Event,
        prior: str,
        new: str,
        command: str,
        actor: Identity,
        authority_reference: str,
        occurred_at: datetime,
        rationale_reference: str | None = None,
    ) -> EventTransition:
        """Append one EventTransition and return it.

        Links it to the Event's last transition by occurred_at then key
        (not locked separately), stores the states' values, and gives it a
        random lineage_reference ("event-transition:" plus a UUID4); the
        reference is not derived from the row. Nothing is validated here
        beyond what the model's create does.
        """
        previous = event.transitions.order_by("occurred_at", "pk").last()
        prior = prior.value if hasattr(prior, "value") else prior
        new = new.value if hasattr(new, "value") else new
        return EventTransition.objects.create(
            event=event,
            from_state=prior,
            to_state=new,
            command=command,
            actor=actor,
            authority_reference=authority_reference,
            rationale_reference=rationale_reference,
            occurred_at=occurred_at,
            previous_transition=previous,
            lineage_reference=f"event-transition:{uuid4()}",
        )

    @transaction.atomic
    def create_event(
        self,
        *,
        identity: User,
        event_id: str,
        title: str,
        evidence_reference: str,
        description: str = "",
        occurred_at: datetime | None = None,
    ) -> Event:
        """Create a DRAFT Event with its transition and evidence atomically.

        Use an aware occurrence time, defaulting to now for a falsy value.
        Require nonblank event_id, title and evidence_reference and strip
        those strings; invalid forms raise Django's ValidationError.
        Ask ContributionAuthority to evaluate create_event against event_id;
        its refusals and capability exceptions propagate.

        Use the returned actor as owner, record creation to DRAFT, and add
        creation evidence. description.strip() is called without a str
        check, so missing or failing strip behaviour propagates. No Event
        full_clean is called by this service. A database uniqueness failure
        for event_id propagates as IntegrityError. Transition and evidence
        saves run their own validation; their ValidationError and database
        errors are not converted.
        """
        occurred_at = self._at(occurred_at)
        event_id = self._text(event_id, "event_id")
        title = self._text(title, "title")
        evidence_reference = self._text(
            evidence_reference,
            "evidence_reference",
        )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action="create_event",
            target=event_id,
            timestamp=occurred_at,
        )
        event = Event.objects.create(
            event_id=event_id,
            title=title,
            description=description.strip(),
            owner=actor,
            created_at=occurred_at,
        )
        creation = self._record_transition(
            event=event,
            prior="creation",
            new=Event.State.DRAFT,
            command="create_event",
            actor=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        EventEvidenceReference.objects.create(
            event=event,
            transition=creation,
            reference=evidence_reference,
            reference_type="creation",
            supplied_by=actor,
            authority_reference=authority_reference,
            occurred_at=occurred_at,
        )
        return event

    @transaction.atomic
    def transition_event(
        self,
        *,
        identity: User,
        event_id: str,
        command: str,
        rationale_reference: str | None = None,
        occurred_at: datetime | None = None,
    ) -> EventTransition:
        """Apply the one allowed command for the Event's current state.

        Inside atomic, resolve the occurrence time and lock the Event;
        a missing row raises Event.DoesNotExist. Allow publish_event from
        DRAFT, activate_event from PUBLISHED, complete_event from ACTIVE,
        and archive_event from COMPLETED. Other commands or states raise
        InvalidEventTransition before authority evaluation.

        Evaluate authority, update state and updated_at, set archived_at
        when archiving, then append and return the transition. Authority,
        capability, model ValidationError and database errors propagate.
        An escaping write failure rolls back this atomic block's changes.

        The service does not compare occurrence with earlier transitions,
        check ownership independently of the capability, or strip rationale.
        updated_at uses the model's automatic time; the transition and
        archived_at use occurred_at. No command replay is provided here.
        """
        occurred_at = self._at(occurred_at)
        event = self._locked(event_id)
        expected = self._TRANSITIONS.get(event.state)
        if expected is None or expected[0] != command:
            raise InvalidEventTransition(
                f"{command} is not permitted from {event.state}"
            )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action=command,
            target=event,
            timestamp=occurred_at,
        )
        prior = event.state
        event.state = expected[1]
        if event.state == Event.State.ARCHIVED:
            event.archived_at = occurred_at
            event.save(update_fields=("state", "archived_at", "updated_at"))
        else:
            event.save(update_fields=("state", "updated_at"))
        return self._record_transition(
            event=event,
            prior=prior,
            new=event.state,
            command=command,
            actor=actor,
            authority_reference=authority_reference,
            rationale_reference=rationale_reference,
            occurred_at=occurred_at,
        )

    @transaction.atomic
    def attach_participant(
        self,
        *,
        identity: User,
        event_id: str,
        participant: Identity,
        occurred_at: datetime | None = None,
    ) -> EventParticipation:
        """Retired: always raises EventParticipationWritesRetired and writes
        nothing. Use governed Event registration instead."""
        raise EventParticipationWritesRetired(
            "EventParticipation writes are retired; use governed Event registration"
        )


__all__ = [
    "EventParticipationWritesRetired",
    "EventService",
    "EventServiceError",
    "InvalidEventTransition",
]