from datetime import datetime, timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import (
    Event,
    EventParticipation,
    EventRegistration,
    EventRegistrationEvidenceReference,
    EventRegistrationTransition,
    Identity,
    ProfileRole,
    Role,
)
from src.intevia.services.contribution_authority import (
    ContributionAuthority,
    NotAuthorised,
)
from src.intevia.services.event_registration_service import (
    AcknowledgementCapabilityUnavailable,
    DuplicateActiveEventRegistration,
    EventRegistrationAuthorityTarget,
    EventRegistrationService,
    IdempotencyConflict,
    InvalidEventRegistration,
    InvalidRegistrationPredecessor,
    InvalidRegistrationTransition,
    PredecessorAlreadySucceeded,
)
from src.intevia.services.event_service import (
    EventParticipationWritesRetired,
    EventService,
)


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)
        self.calls = []

    def authorise(self, *, identity, action, target, timestamp):
        self.calls.append((identity, action, target, timestamp))
        if action in self.denied:
            return None
        return f"authority:{identity.pk}:{action}:{len(self.calls)}"


class EventRegistrationFoundationTests(TestCase):
    def setUp(self):
        self.actor_user, self.actor = self.identity("registration-actor")
        self.other_user, self.other = self.identity("registration-other")
        self.capability = Capability()
        authority = ContributionAuthority(self.capability)
        self.event_service = EventService(authority=authority)
        self.service = EventRegistrationService(authority=authority)
        self.event = self.create_event("event:registration")

    @staticmethod
    def identity(name):
        user = User.objects.create_user(username=name)
        profile = Identity.objects.create(credential=user, access_state=Identity.AccessState.ACTIVE)
        role, _ = Role.objects.get_or_create(name="Event registration actor")
        ProfileRole.objects.create(identity=profile, role=role)
        return user, profile

    def create_event(self, event_id, state=Event.State.PUBLISHED):
        event = Event.objects.create(
            event_id=event_id,
            title=event_id,
            owner=self.actor,
            state=state,
            created_at=NOW,
        )
        return event

    def registration_kwargs(self, **overrides):
        values = {
            "identity": self.actor_user,
            "registration_id": "registration:1",
            "event_id": self.event.event_id,
            "participant": self.actor,
            "evidence_reference": "evidence:registration:1",
            "eligibility_basis_type": (
                EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION
            ),
            "eligibility_basis_reference": "event-state:published",
            "occurred_at": NOW,
        }
        values.update(overrides)
        return values

    def register(self, **overrides):
        return self.service.register(**self.registration_kwargs(**overrides))

    def test_self_registration_persists_receipts_transition_and_evidence(self):
        registration = self.register(
            eligibility_policy_reference=None,
            idempotency_key="registration-command:1",
        )

        transition = registration.transitions.get()
        evidence = transition.evidence_references.get()
        self.assertEqual(registration.origin, EventRegistration.Origin.SELF)
        self.assertEqual(registration.state, EventRegistration.State.REGISTERED)
        self.assertEqual(registration.event_state_at_registration, "published")
        self.assertIsNone(registration.eligibility_policy_reference)
        self.assertEqual(transition.from_state, "new")
        self.assertEqual(transition.to_state, registration.state)
        self.assertEqual(transition.authority_event, self.event)
        self.assertEqual(transition.authority_participant, self.actor)
        self.assertEqual(evidence.transition, transition)
        self.assertFalse(hasattr(evidence, "registration"))
        self.assertIsInstance(
            self.capability.calls[-1][2],
            EventRegistrationAuthorityTarget,
        )

    def test_registration_and_cancellation_obey_all_event_states(self):
        for state in Event.State.values:
            event = self.create_event(f"event:register:{state}", state=state)
            values = self.registration_kwargs(
                registration_id=f"registration:state:{state}",
                event_id=event.event_id,
                evidence_reference=f"evidence:state:{state}",
            )
            if state in (Event.State.PUBLISHED, Event.State.ACTIVE):
                self.service.register(**values)
            else:
                with self.assertRaises(InvalidEventRegistration):
                    self.service.register(**values)

        for state in Event.State.values:
            event = self.create_event(f"event:cancel:{state}")
            registration = self.register(
                registration_id=f"registration:cancel:{state}",
                event_id=event.event_id,
                evidence_reference=f"evidence:cancel:{state}",
            )
            Event.objects.filter(pk=event.pk).update(state=state)
            if state in (Event.State.PUBLISHED, Event.State.ACTIVE):
                self.service.cancel(
                    identity=self.actor_user,
                    registration_id=registration.registration_id,
                    cancellation_basis="actor_decision",
                    basis_source="actor_recorded",
                    occurred_at=NOW,
                )
            else:
                with self.assertRaises(InvalidRegistrationTransition):
                    self.service.cancel(
                        identity=self.actor_user,
                        registration_id=registration.registration_id,
                        cancellation_basis="actor_decision",
                        basis_source="actor_recorded",
                        occurred_at=NOW,
                    )

    def test_third_party_origin_is_truthful_and_acknowledgement_boundary_is_loud(self):
        registration = self.register(
            participant=self.other,
            registration_id="registration:third-party",
            evidence_reference="evidence:third-party",
        )
        transition = registration.transitions.get()
        self.assertEqual(registration.origin, EventRegistration.Origin.THIRD_PARTY)
        self.assertEqual(transition.action_type, "register_third_party")
        self.assertEqual(transition.actor, self.actor)
        self.assertEqual(transition.authority_participant, self.other)

        second_event = self.create_event("event:acknowledgement")
        before = (
            EventRegistration.objects.count(),
            EventRegistrationTransition.objects.count(),
            EventRegistrationEvidenceReference.objects.count(),
        )
        with self.assertRaises(AcknowledgementCapabilityUnavailable):
            self.register(
                event_id=second_event.event_id,
                participant=self.other,
                registration_id="registration:acknowledgement",
                evidence_reference="evidence:acknowledgement",
                acknowledgement_required=True,
            )
        self.assertEqual(
            before,
            (
                EventRegistration.objects.count(),
                EventRegistrationTransition.objects.count(),
                EventRegistrationEvidenceReference.objects.count(),
            ),
        )

    def test_denied_registration_leaves_no_registration_residue(self):
        denied = EventRegistrationService(
            authority=ContributionAuthority(Capability({"register_self"}))
        )
        with self.assertRaises(NotAuthorised):
            denied.register(**self.registration_kwargs())
        self.assertEqual(EventRegistration.objects.count(), 0)
        self.assertEqual(EventRegistrationTransition.objects.count(), 0)
        self.assertEqual(EventRegistrationEvidenceReference.objects.count(), 0)

    def test_cancellation_records_structured_basis_and_is_terminally_idempotent(self):
        registration = self.register()
        transition = self.service.cancel(
            identity=self.actor_user,
            registration_id=registration.registration_id,
            cancellation_basis="participant_request",
            basis_source="participant_supplied",
            evidence_reference="evidence:cancellation",
            idempotency_key="cancel-command:1",
            occurred_at=NOW,
        )
        registration.refresh_from_db()
        self.assertEqual(registration.state, EventRegistration.State.CANCELLED)
        self.assertEqual(transition.to_state, registration.state)
        self.assertEqual(transition.cancellation_basis, "participant_request")
        self.assertEqual(transition.basis_source, "participant_supplied")
        self.assertEqual(transition.evidence_references.count(), 1)

        replay = self.service.cancel(
            identity=self.actor_user,
            registration_id=registration.registration_id,
            cancellation_basis="participant_request",
            basis_source="participant_supplied",
            occurred_at=NOW,
        )
        self.assertEqual(replay, transition)
        self.assertEqual(registration.transitions.count(), 2)
        self.assertEqual(EventRegistrationEvidenceReference.objects.count(), 2)

    def test_same_key_replays_and_conflicting_payload_fails_without_mutation(self):
        first = self.register(idempotency_key="register-command:replay")
        replay = self.register(
            registration_id="registration:ignored-on-replay",
            evidence_reference="evidence:ignored-on-replay",
            idempotency_key="register-command:replay",
        )
        self.assertEqual(replay, first)
        self.assertEqual(EventRegistration.objects.count(), 1)
        self.assertEqual(EventRegistrationTransition.objects.count(), 1)
        self.assertEqual(EventRegistrationEvidenceReference.objects.count(), 1)

        other_event = self.create_event("event:idempotency-conflict")
        with self.assertRaises(IdempotencyConflict):
            self.register(
                event_id=other_event.event_id,
                registration_id="registration:conflict",
                evidence_reference="evidence:conflict",
                idempotency_key="register-command:replay",
            )
        self.assertEqual(EventRegistration.objects.count(), 1)

    def test_reregistration_requires_cancelled_unsucceeded_matching_predecessor(self):
        predecessor = self.register()
        self.service.cancel(
            identity=self.actor_user,
            registration_id=predecessor.registration_id,
            cancellation_basis="event_change",
            basis_source="actor_recorded",
            occurred_at=NOW,
        )
        successor = self.register(
            registration_id="registration:2",
            evidence_reference="evidence:registration:2",
            predecessor=predecessor,
        )
        self.assertEqual(successor.predecessor, predecessor)
        self.assertEqual(successor.transitions.get().action_type, "re_register")

        with self.assertRaises(PredecessorAlreadySucceeded):
            self.register(
                registration_id="registration:3",
                evidence_reference="evidence:registration:3",
                predecessor=predecessor,
            )

        other_event = self.create_event("event:wrong-predecessor")
        with self.assertRaises(InvalidRegistrationPredecessor):
            self.register(
                event_id=other_event.event_id,
                registration_id="registration:wrong-event",
                evidence_reference="evidence:wrong-event",
                predecessor=predecessor,
            )
        with self.assertRaises(InvalidRegistrationPredecessor):
            self.register(
                participant=self.other,
                registration_id="registration:wrong-participant",
                evidence_reference="evidence:wrong-participant",
                predecessor=predecessor,
            )

    def test_predecessor_self_reference_is_database_enforced(self):
        registration = self.register()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EventRegistration.objects.filter(pk=registration.pk).update(
                    predecessor_id=registration.pk
                )
        self.assertTrue(
            EventRegistration.objects.filter(pk=registration.pk).exists()
        )

    def test_active_duplicate_and_missing_lineage_are_distinct(self):
        registration = self.register()
        with self.assertRaises(DuplicateActiveEventRegistration):
            self.register(
                registration_id="registration:duplicate",
                evidence_reference="evidence:duplicate",
            )
        self.service.cancel(
            identity=self.actor_user,
            registration_id=registration.registration_id,
            cancellation_basis="administrative",
            basis_source="actor_recorded",
            occurred_at=NOW,
        )
        with self.assertRaises(InvalidRegistrationPredecessor):
            self.register(
                registration_id="registration:missing-lineage",
                evidence_reference="evidence:missing-lineage",
            )

    def test_integrity_error_translation_is_constraint_specific(self):
        active_error = IntegrityError(
            "UNIQUE constraint failed: core_eventregistration.event_id, "
            "core_eventregistration.participant_id"
        )
        with patch.object(
            EventRegistration.objects,
            "create",
            side_effect=active_error,
        ):
            with self.assertRaises(DuplicateActiveEventRegistration):
                self.register()

        unknown_error = IntegrityError("unknown database integrity failure")
        with patch.object(
            EventRegistration.objects,
            "create",
            side_effect=unknown_error,
        ):
            with self.assertRaises(IntegrityError):
                self.register()

        predecessor = self.register()
        self.service.cancel(
            identity=self.actor_user,
            registration_id=predecessor.registration_id,
            cancellation_basis="administrative",
            basis_source="actor_recorded",
            occurred_at=NOW,
        )
        predecessor_error = IntegrityError(
            "UNIQUE constraint failed: core_eventregistration.predecessor_id"
        )
        with patch.object(
            EventRegistration.objects,
            "create",
            side_effect=predecessor_error,
        ):
            with self.assertRaises(PredecessorAlreadySucceeded):
                self.register(
                    registration_id="registration:predecessor-conflict",
                    evidence_reference="evidence:predecessor-conflict",
                    predecessor=predecessor,
                )

    def test_registration_records_are_immutable_and_non_deletable(self):
        registration = self.register()
        registration.origin = EventRegistration.Origin.THIRD_PARTY
        with self.assertRaises(ValidationError):
            registration.save()
        with self.assertRaises(ValidationError):
            registration.delete()
        transition = registration.transitions.get()
        with self.assertRaises(ValidationError):
            transition.save()
        with self.assertRaises(ValidationError):
            transition.delete()
        evidence = transition.evidence_references.get()
        with self.assertRaises(ValidationError):
            evidence.save()
        with self.assertRaises(ValidationError):
            evidence.delete()

    def test_participation_writes_are_retired_but_historical_reads_remain(self):
        historical = EventParticipation(
            event=self.event,
            participant=self.other,
            attached_by=self.actor,
            authority_reference="authority:historical",
            attached_at=NOW,
        )
        EventParticipation.objects.bulk_create([historical])
        self.assertEqual(self.event.participations.get().participant, self.other)

        with self.assertRaises(EventParticipationWritesRetired):
            self.event_service.attach_participant(
                identity=self.actor_user,
                event_id=self.event.event_id,
                participant=self.other,
                occurred_at=NOW,
            )
        with self.assertRaises(ValidationError):
            EventParticipation.objects.create(
                event=self.event,
                participant=self.actor,
                attached_by=self.actor,
                authority_reference="authority:new",
                attached_at=NOW,
            )
        with self.assertRaises(ValidationError):
            EventParticipation(
                event=self.event,
                participant=self.actor,
                attached_by=self.actor,
                authority_reference="authority:direct-save",
                attached_at=NOW,
            ).save()
        self.assertEqual(EventParticipation.objects.count(), 1)

    def test_operational_query_requires_registration_and_event_state(self):
        registration = self.register()
        self.assertIn(
            registration,
            self.service.operationally_active(self.event.event_id),
        )
        Event.objects.filter(pk=self.event.pk).update(state=Event.State.COMPLETED)
        self.assertFalse(
            self.service.operationally_active(self.event.event_id).exists()
        )
