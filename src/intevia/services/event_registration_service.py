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
    Identity,
    ProfileRole,
)
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)


class EventRegistrationError(Exception):
    """Base failure for governed Event registration."""


class InvalidEventRegistration(EventRegistrationError):
    """The Event is not open for registration (not PUBLISHED or
    ACTIVE)."""
    pass


class DuplicateActiveEventRegistration(EventRegistrationError):
    """The participant already has a REGISTERED registration for the
    Event, found before the write or reported by the database."""
    pass


class InvalidRegistrationTransition(EventRegistrationError):
    """A cancellation is not allowed: the Event is not PUBLISHED or
    ACTIVE, or the registration is neither REGISTERED nor CANCELLED. An
    already CANCELLED registration is returned, not refused."""
    pass


class InvalidRegistrationPredecessor(EventRegistrationError):
    """A registration's predecessor is missing or wrong: a new root when
    an earlier registration exists, a predecessor of another Event or
    participant, or one that is not CANCELLED."""
    pass


class PredecessorAlreadySucceeded(EventRegistrationError):
    """The cancelled predecessor already has a successor, found before
    the write or reported by the database."""
    pass


class AcknowledgementCapabilityUnavailable(EventRegistrationError):
    """A third-party registration needs the participant's prior
    acknowledgement, which this service cannot obtain."""
    pass


class IdempotencyConflict(EventRegistrationError):
    """An idempotency key the actor already used for this action is
    reused for a different Event, participant or predecessor."""
    pass


@dataclass(frozen=True, slots=True)
class EventRegistrationAuthorityTarget:
    """Carry the Event-registration target for authority and eligibility.

    Hold the Event, participant, predecessor, origin, acknowledgement
    flag and optional correlation identity. register sets that identity
    to its idempotency key; cancel leaves it as None. This frozen
    dataclass does not validate the values or grant authority.
    """
    event: Event
    participant: Identity
    predecessor: EventRegistration | None
    origin: str
    acknowledgement_required: bool
    correlation_identity: str | None = None


class EventRegistrationService:
    """Persist Event-owned registration outcomes under external authority."""

    _REGISTERABLE_EVENT_STATES = frozenset(
        {Event.State.PUBLISHED, Event.State.ACTIVE}
    )
    _CANCELLABLE_EVENT_STATES = _REGISTERABLE_EVENT_STATES

    def __init__(self, *, authority: ContributionAuthority, eligibility=None) -> None:
        """Keep the authority and the optional eligibility source.

        authority must be a ContributionAuthority (isinstance; TypeError);
        an eligibility source, when given, must have a callable
        determine_eligibility (TypeError).
        """
        if not isinstance(authority, ContributionAuthority):
            raise TypeError("authority must preserve the existing capability contract")
        if eligibility is not None and not callable(
            getattr(eligibility, "determine_eligibility", None)
        ):
            raise TypeError("eligibility must implement determine_eligibility")
        self.authority = authority
        self.eligibility = eligibility

    @staticmethod
    def _at(value: datetime | None) -> datetime:
        """Use the supplied time, or timezone.now() when the value is falsy;
        require tzinfo and a UTC offset, else Django's ValidationError. No
        datetime type check is made, and the value is not converted."""
        value = value or timezone.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("timestamp must be timezone-aware")
        return value

    @staticmethod
    def _text(value: str, name: str, *, required: bool = True) -> str:
        """Return the text stripped. A non-str raises Django's
        ValidationError, as does blank text when required. No length or
        format is checked here."""
        if not isinstance(value, str):
            raise ValidationError(f"{name} must be text")
        value = value.strip()
        if required and not value:
            raise ValidationError(f"{name} is required")
        return value

    @staticmethod
    def _replay_actor(identity: User) -> Identity:
        """Resolve the acting Identity from the credential without locking.

        Raise NotAuthorised for a non-User or inactive credential, no unique
        Identity, a non-ACTIVE Identity, or no ProfileRole row. The role
        query tests existence only. This does not invoke the capability.
        Paths that reach ContributionAuthority.evaluate repeat actor checks
        and lock the Identity there; an early replay does not.
        """
        if not isinstance(identity, User) or not identity.is_active:
            raise NotAuthorised("an active Django identity is required")
        try:
            actor = Identity.objects.get(credential=identity)
        except (Identity.DoesNotExist, Identity.MultipleObjectsReturned) as exc:
            raise NotAuthorised(
                "credential must resolve to exactly one Identity"
            ) from exc
        if actor.access_state != Identity.AccessState.ACTIVE:
            raise NotAuthorised("an active Identity is required")
        if not ProfileRole.objects.filter(identity=actor).exists():
            raise NotAuthorised("an active role assignment is required")
        return actor

    @staticmethod
    def _constraint_name(error: IntegrityError) -> str | None:
        """Read a driver constraint name or recognise a fallback column list.

        Return a truthy __cause__.diag.constraint_name without restricting
        it to the service's known names. Otherwise search str(error) for
        three SQLite-style column lists: active Event/participant,
        registration predecessor, and transition actor/action/key. Return
        the first matching name, or None. No backend or error-type check is
        made here; callers decide which recognised constraints to handle.
        """
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
        actor: Identity,
        action_type: str,
        idempotency_key: str | None,
        event_id: int,
        participant_id: int,
        predecessor_id: int | None,
    ) -> EventRegistrationTransition | None:
        """Find a transition for the actor, action and key, or return None.

        Return None for a None key or no matching row. Compare its authority
        Event, participant and predecessor ids with the supplied tuple;
        a mismatch raises IdempotencyConflict. Return the transition with
        its registration selected, without locking either row.

        No credential, capability, state or lineage validation occurs here.
        The registration id, evidence, times, eligibility receipt,
        acknowledgement flag and cancellation basis/source are not compared.
        This is a bounded tuple match, not a complete request fingerprint.
        Database and other unhandled errors propagate.
        """
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
        actor: Identity,
        occurred_at: datetime,
    ) -> EventRegistrationEvidenceReference:
        """Record one evidence reference on a transition. The model's save
        validates it; a repeated reference for the same transition is
        refused by the database. Errors propagate."""
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
        participant: Identity,
        evidence_reference: str,
        eligibility_basis_type: str | None,
        eligibility_basis_reference: str | None,
        eligibility_policy_reference: str | None = None,
        predecessor: EventRegistration | None = None,
        acknowledgement_required: bool = False,
        idempotency_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> EventRegistration:
        """Register a participant atomically, or return a matched replay row.

        Resolve an aware time, defaulting to now for a falsy value. Require
        nonblank registration_id and evidence_reference, and a nonblank key
        if supplied. Without an eligibility source, require a nonblank basis
        reference and a governed basis type; an optional policy reference
        must be nonblank. With a source, reject supplied eligibility fields.
        These checks raise Django ValidationError. The participant check
        requires an Identity with a non-None pk, not a fresh existence query.

        Resolve the actor or raise NotAuthorised. Derive SELF or THIRD_PARTY
        origin; a predecessor selects RE_REGISTER. Look up the Event, letting
        Event.DoesNotExist propagate. Check replay before and after locking
        the Event. A matching actor/action/key and authority tuple returns
        the stored registration as read, possibly now CANCELLED; a different
        tuple raises IdempotencyConflict. These returns bypass capability,
        eligibility and Event/predecessor-state checks, not input validation.

        For a new write, require PUBLISHED or ACTIVE, else raise
        InvalidEventRegistration. Without a predecessor, an active row raises
        DuplicateActiveEventRegistration; other history raises
        InvalidRegistrationPredecessor. A supplied predecessor must belong
        to this Event/participant and be CANCELLED, else raise
        InvalidRegistrationPredecessor; an existing successor raises
        PredecessorAlreadySucceeded.

        Evaluate authority. If an eligibility source is present, require
        nonblank receipt references, an aware resolved evaluation time equal
        to the command time, and EVENT_POLICY or EVENT_CONFIGURATION;
        invalid receipt values raise ValidationError. Provider and malformed
        receipt errors propagate. THIRD_PARTY with a truthy acknowledgement
        flag raises AcknowledgementCapabilityUnavailable.

        Insert the registration, REGISTERED transition and evidence in an
        inner atomic block. Model validation errors propagate. A recognised
        database idempotency clash returns a replay only if one is found
        and matches; a mismatch raises IdempotencyConflict and no match
        re-raises IntegrityError. Recognised active-row and successor clashes
        map to their service errors; other database failures propagate.

        There is no independent visibility, participant-activity, future-time
        or chronological-lineage check. Replay compares only its documented
        tuple, not every input. Authority and eligibility rules belong to
        the supplied providers; references do not reconstruct those policies.
        """
        occurred_at = self._at(occurred_at)
        registration_id = self._text(registration_id, "registration_id")
        evidence_reference = self._text(evidence_reference, "evidence_reference")
        if self.eligibility is None:
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
        elif any(
            value is not None
            for value in (
                eligibility_basis_type,
                eligibility_basis_reference,
                eligibility_policy_reference,
            )
        ):
            raise ValidationError("trusted eligibility fields must not be supplied")
        idempotency_key = (
            self._text(idempotency_key, "idempotency_key")
            if idempotency_key is not None
            else None
        )
        if not isinstance(participant, Identity) or participant.pk is None:
            raise ValidationError("participant must be a persisted Identity")
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
            correlation_identity=idempotency_key,
        )
        actor, authority_reference = self.authority.evaluate(
            identity=identity,
            action=action_type,
            target=target,
            timestamp=occurred_at,
        )
        eligibility_evaluated_at = occurred_at
        if self.eligibility is not None:
            receipt = self.eligibility.determine_eligibility(
                identity=actor,
                action=action_type,
                target=target,
                timestamp=occurred_at,
            )
            eligibility_basis_type = receipt.basis_type
            eligibility_basis_reference = self._text(
                receipt.basis_reference,
                "eligibility_basis_reference",
            )
            eligibility_policy_reference = self._text(
                receipt.policy_reference,
                "eligibility_policy_reference",
            )
            eligibility_evaluated_at = self._at(receipt.evaluated_at)
            if eligibility_evaluated_at != occurred_at:
                raise ValidationError("eligibility receipt timestamp is not current")
            if eligibility_basis_type not in {
                EventRegistration.EligibilityBasisType.EVENT_POLICY,
                EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION,
            }:
                raise ValidationError("eligibility receipt requires review")
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
                    eligibility_evaluated_at=eligibility_evaluated_at,
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
        """Cancel atomically, or return an earlier transition without writing.

        Resolve an aware time and require nonblank registration_id. Require
        governed cancellation_basis and basis_source; optional evidence and
        key must be nonblank text when supplied. Invalid forms raise Django
        ValidationError; native attribute/time-zone errors propagate.
        Resolve the actor or raise NotAuthorised, then fetch the registration
        by id; a missing row raises EventRegistration.DoesNotExist.

        A matching actor/CANCEL/key and authority tuple returns its
        transition before locks, capability or state checks; a different
        tuple raises IdempotencyConflict. Basis, source, evidence and time
        are not compared. Otherwise lock Event then registration, and
        evaluate authority before state checks. There is no second replay
        check after locking. Lookup, authority and capability errors pass
        through. The authority target does not receive the request key.

        Require a PUBLISHED or ACTIVE Event, else raise
        InvalidRegistrationTransition. Select the registration's latest
        transition by occurred_at then pk. If already CANCELLED, return that
        row without writing or recording this key or basis; it need not be
        the cancellation row, and missing history yields None. Otherwise
        require REGISTERED, else raise InvalidRegistrationTransition.

        In an inner atomic block, append a CANCELLED transition linked to
        that selected row, add evidence if supplied, and set the stored
        registration state with queryset.update, bypassing its save hook.
        Return the new transition. Model validation and database failures
        propagate, except that a recognised idempotency constraint failure
        tries replay: return a match, raise IdempotencyConflict for a changed
        tuple, or re-raise IntegrityError when no match is found.

        No independent ownership, chronological ordering or full-history
        check is made. The capability decides who may cancel; the service
        does not verify the truth of the supplied cancellation basis/source.
        """
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
        """Return the REGISTERED registrations of an Event that is PUBLISHED
        or ACTIVE, as an unevaluated queryset; empty for an unknown id. No
        authority or visibility check is made."""
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