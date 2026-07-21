"""Governed Event registration commands and immutable lineage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    Event,
    EventRegistration,
    EventRegistrationEvidenceReference,
    EventRegistrationTransition,
    Profile,
    ProfileRole,
)
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)


class EventRegistrationError(Exception):
    """Base failure for governed Event registration."""


class InvalidEventRegistration(EventRegistrationError):
    pass


class DuplicateActiveEventRegistration(EventRegistrationError):
    pass


class InvalidRegistrationTransition(EventRegistrationError):
    pass


class InvalidRegistrationPredecessor(EventRegistrationError):
    pass


class PredecessorAlreadySucceeded(EventRegistrationError):
    pass


class AcknowledgementCapabilityUnavailable(EventRegistrationError):
    pass


class IdempotencyConflict(EventRegistrationError):
    pass


@dataclass(frozen=True, slots=True)
class EventRegistrationAuthorityTarget:
    event: Event
    participant: Profile
    predecessor: EventRegistration | None
    origin: str
    acknowledgement_required: bool


class EventRegistrationService:
    """Persist Event-owned registration outcomes under external authority."""

    _REGISTERABLE_EVENT_STATES = frozenset(
        {Event.State.PUBLISHED, Event.State.ACTIVE}
    )
    _CANCELLABLE_EVENT_STATES = _REGISTERABLE_EVENT_STATES

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
    def _text(value: str, name: str, *, required: bool = True) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{name} must be text")
        value = value.strip()
        if required and not value:
            raise ValidationError(f"{name} is required")
        return value

    @staticmethod
    def _replay_actor(identity: User) -> Profile:
        if not isinstance(identity, User) or not identity.is_active:
            raise NotAuthorised("an active Django identity is required")
        try:
            actor = Profile.objects.get(user=identity)
        except (Profile.DoesNotExist, Profile.MultipleObjectsReturned) as exc:
            raise NotAuthorised(
                "identity must resolve to exactly one Profile"
            ) from exc
        if not ProfileRole.objects.filter(profile=actor).exists():
            raise NotAuthorised("an active role assignment is required")
        return actor

    @staticmethod
    def _constraint_name(error: IntegrityError) -> str | None:
        cause = error.__cause__
        diagnostics = getattr(cause, "diag", None)
        name = getattr(diagnostics, "constraint_name", None)
        if name:
            return name
        message = str(error)
        sqlite_constraints = {
            "core_eventregistration.event_id, core_eventregistration.participant_id": "one_active_event_registration",
            "core_eventregistration.predecessor_id": "one_event_registration_successor",
            "core_eventregistrationtransition.actor_id, core_eventregistrationtransition.action_type, core_eventregistrationtransition.idempotency_key": "unique_event_reg_idempotency",
        }
        return next(
            (name for columns, name in sqlite_constraints.items() if columns in message),
            None,
        )

    @staticmethod
    def _replay(
        *,
        actor: Profile,
        action_type: str,
        idempotency_key: str | None,
        event_id: int,
        participant_id: int,
        predecessor_id: int | None,
    ) -> EventRegistrationTransition | None:
        if idempotency_key is None:
            return None
        transition = (
            EventRegistrationTransition.objects.select_related("registration")
            .filter(
                actor=actor,
                action_type=action_type,
                idempotency_key=idempotency_key,
            )
            .first()
        )
        if transition is None:
            return None
        payload = (
            transition.authority_event_id,
            transition.authority_participant_id,
            transition.authority_predecessor_id,
        )
        if payload != (event_id, participant_id, predecessor_id):
            raise IdempotencyConflict(
                "idempotency key was already used with a different payload"
            )
        return transition

    @staticmethod
    def _evidence(
        *,
        transition: EventRegistrationTransition,
        reference: str,
        reference_type: str,
        actor: Profile,
        occurred_at: datetime,
    ) -> EventRegistrationEvidenceReference:
        return EventRegistrationEvidenceReference.objects.create(
            transition=transition,
            reference=reference,
            reference_type=reference_type,
            supplied_by=actor,
            occurred_at=occurred_at,
        )

    @transaction.atomic
    def register(
        self,
        *,
        identity: User,
        registration_id: str,
        event_id: str,
        participant: Profile,
        evidence_reference: str,
        eligibility_basis_type: str,
        eligibility_basis_reference: str,
        eligibility_policy_reference: str | None = None,
        predecessor: EventRegistration | None = None,
        acknowledgement_required: bool = False,
        idempotency_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> EventRegistration:
        occurred_at = self._at(occurred_at)
        registration_id = self._text(registration_id, "registration_id")
        evidence_reference = self._text(evidence_reference, "evidence_reference")
        eligibility_basis_reference = self._text(
            eligibility_basis_reference,
            "eligibility_basis_reference",
        )
        if eligibility_basis_type not in EventRegistration.EligibilityBasisType.values:
            raise ValidationError("eligibility_basis_type is not governed")
        if eligibility_policy_reference is not None:
            eligibility_policy_reference = self._text(
                eligibility_policy_reference,
                "eligibility_policy_reference",
            )
        idempotency_key = (
            self._text(idempotency_key, "idempotency_key")
            if idempotency_key is not None
            else None
        )
        if not isinstance(participant, Profile) or participant.pk is None:
            raise ValidationError("participant must be a persisted Profile")
        actor = self._replay_actor(identity)
        origin = (
            EventRegistration.Origin.SELF
            if actor.pk == participant.pk
            else EventRegistration.Origin.THIRD_PARTY
        )
        action_type = (
            EventRegistrationTransition.ActionType.RE_REGISTER
            if predecessor is not None
            else (
                EventRegistrationTransition.ActionType.REGISTER_SELF
                if origin == EventRegistration.Origin.SELF
                else EventRegistrationTransition.ActionType.REGISTER_THIRD_PARTY
            )
        )
        event = Event.objects.get(event_id=event_id)
        predecessor_id = predecessor.pk if predecessor is not None else None
        replay = self._replay(
            actor=actor,
            action_type=action_type,
            idempotency_key=idempotency_key,
            event_id=event.pk,
            participant_id=participant.pk,
            predecessor_id=predecessor_id,
        )
        if replay is not None:
            return replay.registration

        event = Event.objects.select_for_update().get(pk=event.pk)
        replay = self._replay(
            actor=actor,
            action_type=action_type,
            idempotency_key=idempotency_key,
            event_id=event.pk,
            participant_id=participant.pk,
            predecessor_id=predecessor_id,
        )
        if replay is not None:
            return replay.registration
        registrations = EventRegistration.objects.select_for_update().filter(
            event=event,
            participant=participant,
        )
        if event.state not in self._REGISTERABLE_EVENT_STATES:
            raise InvalidEventRegistration(
                f"registration is not permitted while Event is {event.state}"
            )
        if predecessor is None:
            if registrations.filter(
                state=EventRegistration.State.REGISTERED
            ).exists():
                raise DuplicateActiveEventRegistration(
                    "an active registration already exists"
                )
            if registrations.exists():
                raise InvalidRegistrationPredecessor(
                    "re-registration requires the cancelled predecessor"
                )
        else:
            predecessor = registrations.filter(pk=predecessor.pk).first()
            if predecessor is None:
                raise InvalidRegistrationPredecessor(
                    "predecessor must belong to the same Event and participant"
                )
            if predecessor.state != EventRegistration.State.CANCELLED:
                raise InvalidRegistrationPredecessor(
                    "predecessor must be terminal and cancelled"
                )
            if EventRegistration.objects.filter(
                predecessor=predecessor
            ).exists():
                raise PredecessorAlreadySucceeded(
                    "predecessor already has a successor"
                )

        target = EventRegistrationAuthorityTarget(
            event=event,
            participant=participant,
            predecessor=predecessor,
            origin=origin,
            acknowledgement_required=acknowledgement_required,
        )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action=action_type,
            target=target,
            timestamp=occurred_at,
        )
        if origin == EventRegistration.Origin.THIRD_PARTY and acknowledgement_required:
            raise AcknowledgementCapabilityUnavailable(
                "prior participant acknowledgement is required but unavailable"
            )

        try:
            with transaction.atomic():
                registration = EventRegistration.objects.create(
                    registration_id=registration_id,
                    event=event,
                    participant=participant,
                    origin=origin,
                    predecessor=predecessor,
                    event_state_at_registration=event.state,
                    eligibility_policy_reference=eligibility_policy_reference,
                    eligibility_basis_type=eligibility_basis_type,
                    eligibility_basis_reference=eligibility_basis_reference,
                    eligibility_evaluated_at=occurred_at,
                    registered_at=occurred_at,
                )
                transition = EventRegistrationTransition.objects.create(
                    registration=registration,
                    from_state="new",
                    to_state=EventRegistration.State.REGISTERED,
                    action_type=action_type,
                    actor=actor,
                    authority_reference=authority_reference,
                    authority_event=event,
                    authority_participant=participant,
                    authority_predecessor=predecessor,
                    authority_evaluated_at=occurred_at,
                    idempotency_key=idempotency_key,
                    occurred_at=occurred_at,
                    lineage_reference=f"event-registration-transition:{uuid4()}",
                )
                self._evidence(
                    transition=transition,
                    reference=evidence_reference,
                    reference_type="registration",
                    actor=actor,
                    occurred_at=occurred_at,
                )
        except IntegrityError as error:
            constraint = self._constraint_name(error)
            if constraint == "unique_event_reg_idempotency":
                replay = self._replay(
                    actor=actor,
                    action_type=action_type,
                    idempotency_key=idempotency_key,
                    event_id=event.pk,
                    participant_id=participant.pk,
                    predecessor_id=predecessor_id,
                )
                if replay is not None:
                    return replay.registration
            if constraint == "one_active_event_registration":
                raise DuplicateActiveEventRegistration(
                    "an active registration already exists"
                ) from error
            if constraint == "one_event_registration_successor":
                raise PredecessorAlreadySucceeded(
                    "predecessor already has a successor"
                ) from error
            raise
        return registration

    @transaction.atomic
    def cancel(
        self,
        *,
        identity: User,
        registration_id: str,
        cancellation_basis: str,
        basis_source: str,
        evidence_reference: str | None = None,
        idempotency_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> EventRegistrationTransition:
        occurred_at = self._at(occurred_at)
        registration_id = self._text(registration_id, "registration_id")
        if cancellation_basis not in EventRegistrationTransition.CancellationBasis.values:
            raise ValidationError("cancellation_basis is not governed")
        if basis_source not in EventRegistrationTransition.BasisSource.values:
            raise ValidationError("basis_source is not governed")
        evidence_reference = (
            self._text(evidence_reference, "evidence_reference")
            if evidence_reference is not None
            else None
        )
        idempotency_key = (
            self._text(idempotency_key, "idempotency_key")
            if idempotency_key is not None
            else None
        )
        actor = self._replay_actor(identity)
        registration = EventRegistration.objects.select_related("event").get(
            registration_id=registration_id
        )
        replay = self._replay(
            actor=actor,
            action_type=EventRegistrationTransition.ActionType.CANCEL,
            idempotency_key=idempotency_key,
            event_id=registration.event_id,
            participant_id=registration.participant_id,
            predecessor_id=registration.predecessor_id,
        )
        if replay is not None:
            return replay

        event = Event.objects.select_for_update().get(pk=registration.event_id)
        registration = EventRegistration.objects.select_for_update().get(
            pk=registration.pk
        )
        target = EventRegistrationAuthorityTarget(
            event=event,
            participant=registration.participant,
            predecessor=registration.predecessor,
            origin=registration.origin,
            acknowledgement_required=False,
        )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action=EventRegistrationTransition.ActionType.CANCEL,
            target=target,
            timestamp=occurred_at,
        )
        if event.state not in self._CANCELLABLE_EVENT_STATES:
            raise InvalidRegistrationTransition(
                f"cancellation is not permitted while Event is {event.state}"
            )
        previous = registration.transitions.order_by("occurred_at", "pk").last()
        if registration.state == EventRegistration.State.CANCELLED:
            return previous
        if registration.state != EventRegistration.State.REGISTERED:
            raise InvalidRegistrationTransition(
                f"cannot cancel registration from {registration.state}"
            )

        try:
            with transaction.atomic():
                transition = EventRegistrationTransition.objects.create(
                    registration=registration,
                    from_state=registration.state,
                    to_state=EventRegistration.State.CANCELLED,
                    action_type=EventRegistrationTransition.ActionType.CANCEL,
                    actor=actor,
                    authority_reference=authority_reference,
                    authority_event=event,
                    authority_participant=registration.participant,
                    authority_predecessor=registration.predecessor,
                    authority_evaluated_at=occurred_at,
                    idempotency_key=idempotency_key,
                    cancellation_basis=cancellation_basis,
                    basis_source=basis_source,
                    occurred_at=occurred_at,
                    lineage_reference=f"event-registration-transition:{uuid4()}",
                    previous_transition=previous,
                )
                if evidence_reference is not None:
                    self._evidence(
                        transition=transition,
                        reference=evidence_reference,
                        reference_type="cancellation",
                        actor=actor,
                        occurred_at=occurred_at,
                    )
                EventRegistration.objects.filter(pk=registration.pk).update(
                    state=EventRegistration.State.CANCELLED
                )
        except IntegrityError as error:
            if self._constraint_name(error) == "unique_event_reg_idempotency":
                replay = self._replay(
                    actor=actor,
                    action_type=EventRegistrationTransition.ActionType.CANCEL,
                    idempotency_key=idempotency_key,
                    event_id=event.pk,
                    participant_id=registration.participant_id,
                    predecessor_id=registration.predecessor_id,
                )
                if replay is not None:
                    return replay
            raise
        return transition

    @staticmethod
    def operationally_active(event_id: str):
        return EventRegistration.objects.filter(
            event__event_id=event_id,
            state=EventRegistration.State.REGISTERED,
            event__state__in=(Event.State.PUBLISHED, Event.State.ACTIVE),
        )


__all__ = [
    "AcknowledgementCapabilityUnavailable",
    "DuplicateActiveEventRegistration",
    "EventRegistrationAuthorityTarget",
    "EventRegistrationError",
    "EventRegistrationService",
    "IdempotencyConflict",
    "InvalidEventRegistration",
    "InvalidRegistrationPredecessor",
    "InvalidRegistrationTransition",
    "PredecessorAlreadySucceeded",
]