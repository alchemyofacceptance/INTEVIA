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
        if not isinstance(authority, ContributionAuthority):
            raise TypeError("authority must preserve the existing capability contract")
        self.authority = authority

    @staticmethod
    def _at(value: datetime | None) -> datetime:
        value = value or timezone.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("timestamp must be timezone-aware")
        return value

    @staticmethod
    def _text(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{name} is required")
        return value.strip()

    @staticmethod
    def _locked(event_id: str) -> Event:
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
        raise EventParticipationWritesRetired(
            "EventParticipation writes are retired; use governed Event registration"
        )


__all__ = [
    "EventParticipationWritesRetired",
    "EventService",
    "EventServiceError",
    "InvalidEventTransition",
]