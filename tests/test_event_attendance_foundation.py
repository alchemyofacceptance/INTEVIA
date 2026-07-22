from datetime import datetime, timedelta, timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import (
    Event,
    EventAttendance,
    EventAttendanceEligibilityReceipt,
    EventAttendanceEvidenceReference,
    EventAttendanceTransition,
    EventParticipation,
    EventRegistration,
    Identity,
)
from src.intevia.services.event_attendance_service import (
    AttendanceEvidence,
    AttendanceIdempotencyConflict,
    AttendanceNotAuthorised,
    DuplicateEventAttendance,
    EventAttendanceAuthority,
    EventAttendanceService,
    InvalidAttendance,
    InvalidAttendanceTransition,
    WalkInEligibility,
)
from src.intevia.services.event_read_service import (
    EventNotVisible,
    EventReadService,
)


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
CORRECTED = NOW - timedelta(minutes=5)


class Capability:
    def __init__(self, denied=()):
        self.denied = set(denied)
        self.targets = []

    def authorise(self, *, identity, action, target, timestamp):
        self.targets.append(target)
        if action in self.denied:
            return None
        return f"authority:attendance:{identity.pk}:{action}"


class AsyncCapability:
    async def authorise(self, *, identity, action, target, timestamp):
        return "authority:async"


class EventAttendanceFoundationTests(TestCase):
    def setUp(self):
        self.actor_user = User.objects.create_user(username="attendance-actor")
        self.actor = Identity.objects.create(
            credential=self.actor_user,
            access_state=Identity.AccessState.ACTIVE,
        )
        self.subject_user = User.objects.create_user(username="attendance-subject")
        self.subject = Identity.objects.create(
            credential=self.subject_user,
            access_state=Identity.AccessState.ACTIVE,
        )
        self.other_user = User.objects.create_user(username="attendance-other")
        self.other = Identity.objects.create(
            credential=self.other_user,
            access_state=Identity.AccessState.ACTIVE,
        )
        self.event = Event.objects.create(
            event_id="event:attendance",
            title="Attendance",
            owner=self.actor,
            state=Event.State.ACTIVE,
            created_at=NOW,
        )
        self.registration = EventRegistration.objects.create(
            registration_id="registration:attendance",
            event=self.event,
            participant=self.subject,
            state=EventRegistration.State.REGISTERED,
            origin=EventRegistration.Origin.THIRD_PARTY,
            event_state_at_registration=Event.State.ACTIVE,
            eligibility_basis_type=(
                EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION
            ),
            eligibility_basis_reference="event:attendance:eligible",
            eligibility_evaluated_at=NOW,
            registered_at=NOW,
        )
        self.capability = Capability()
        self.service = EventAttendanceService(
            authority=EventAttendanceAuthority(self.capability),
            clock=lambda: NOW,
        )

    @staticmethod
    def evidence(reference="evidence:actor-attestation"):
        return (
            AttendanceEvidence(
                reference=reference,
                evidence_type="actor_assertion",
                provenance="authorised_actor",
            ),
        )

    def record_registered(self, **overrides):
        values = {
            "identity": self.actor_user,
            "attendance_id": "attendance:registered",
            "event_id": self.event.event_id,
            "subject": self.subject,
            "origin": EventAttendance.Origin.REGISTERED,
            "supporting_registration": self.registration,
            "evidence": self.evidence(),
            "idempotency_key": "attendance:record:registered",
        }
        values.update(overrides)
        return self.service.record_attendance(**values)

    def record_walk_in(self, **overrides):
        values = {
            "identity": self.actor_user,
            "attendance_id": "attendance:walk-in",
            "event_id": self.event.event_id,
            "subject": self.other,
            "origin": EventAttendance.Origin.GOVERNED_WALK_IN,
            "evidence": self.evidence("evidence:walk-in-attestation"),
            "walk_in_eligibility": WalkInEligibility(
                basis_reference="eligibility:walk-in:governed"
            ),
            "idempotency_key": "attendance:record:walk-in",
        }
        values.update(overrides)
        return self.service.record_attendance(**values)

    def test_registered_record_persists_positive_presence_and_lineage(self):
        attendance = self.record_registered()

        self.assertEqual(attendance.status, EventAttendance.Status.PRESENT)
        self.assertEqual(attendance.observed_at, NOW)
        self.assertEqual(attendance.supporting_registration, self.registration)
        self.assertEqual(attendance.origin, EventAttendance.Origin.REGISTERED)
        transition = attendance.transitions.get()
        self.assertEqual(transition.action, EventAttendanceTransition.Action.RECORD)
        self.assertEqual(transition.from_status, "unrecorded")
        self.assertEqual(transition.to_status, EventAttendance.Status.PRESENT)
        self.assertEqual(transition.sequence, 1)
        self.assertEqual(transition.actor, self.actor)
        self.assertEqual(transition.basis, EventAttendance.Origin.REGISTERED)
        target = self.capability.targets[-1]
        self.assertEqual(target.actor, self.actor)
        self.assertEqual(target.subject, self.subject)
        self.assertEqual(target.event, self.event)
        self.assertEqual(target.supporting_registration, self.registration)
        self.assertEqual(target.event_state, Event.State.ACTIVE)
        self.assertEqual(target.registration_state, EventRegistration.State.REGISTERED)
        self.assertEqual(target.actor_access_state, Identity.AccessState.ACTIVE)
        self.assertEqual(target.requested_outcome, EventAttendance.Status.PRESENT)

    def test_async_capability_is_rejected_before_execution(self):
        with self.assertRaisesRegex(TypeError, "local and synchronous"):
            EventAttendanceAuthority(AsyncCapability())

    def test_governed_walk_in_persists_complete_receipt(self):
        attendance = self.record_walk_in()

        self.assertIsNone(attendance.supporting_registration)
        receipt = attendance.walk_in_eligibility_receipt
        self.assertEqual(
            receipt.basis_type,
            EventAttendanceEligibilityReceipt.BasisType.GOVERNED_WALK_IN,
        )
        self.assertEqual(receipt.event, self.event)
        self.assertEqual(receipt.subject, self.other)
        self.assertEqual(receipt.actor, self.actor)
        self.assertEqual(receipt.event_state_snapshot, Event.State.ACTIVE)
        self.assertEqual(receipt.record_transition, attendance.transitions.get())

    def test_actor_attestation_is_typed_and_not_independent_proof(self):
        attendance = self.record_registered()
        evidence = attendance.transitions.get().evidence_references.get()

        self.assertEqual(
            evidence.classification,
            EventAttendanceEvidenceReference.Classification.ACTOR_ATTESTATION,
        )
        self.assertEqual(evidence.provenance, "authorised_actor")

    def test_multiple_actor_attestations_are_supported(self):
        attendance = self.record_registered(
            evidence=self.evidence("evidence:one") + self.evidence("evidence:two")
        )
        self.assertEqual(attendance.transitions.get().evidence_references.count(), 2)

    def test_authority_denial_has_no_residue(self):
        service = EventAttendanceService(
            authority=EventAttendanceAuthority(Capability({"record_attendance"})),
            clock=lambda: NOW,
        )
        with self.assertRaises(AttendanceNotAuthorised):
            service.record_attendance(
                identity=self.actor_user,
                attendance_id="attendance:denied",
                event_id=self.event.event_id,
                subject=self.subject,
                origin=EventAttendance.Origin.REGISTERED,
                supporting_registration=self.registration,
                evidence=self.evidence(),
                idempotency_key="attendance:denied",
            )
        self.assertFalse(EventAttendance.objects.exists())
        self.assertFalse(EventAttendanceTransition.objects.exists())

    def test_actor_cannot_record_self(self):
        with self.assertRaises(AttendanceNotAuthorised):
            self.record_registered(subject=self.actor)
        self.assertFalse(EventAttendance.objects.exists())

    def test_first_record_requires_active_event(self):
        for state in (
            Event.State.DRAFT,
            Event.State.PUBLISHED,
            Event.State.COMPLETED,
            Event.State.ARCHIVED,
        ):
            with self.subTest(state=state):
                Event.objects.filter(pk=self.event.pk).update(state=state)
                self.event.refresh_from_db()
                with self.assertRaises(InvalidAttendance):
                    self.record_registered(
                        attendance_id=f"attendance:{state}",
                        idempotency_key=f"attendance:{state}",
                    )
                self.assertFalse(EventAttendance.objects.exists())

    def test_cancelled_registration_cannot_support_first_record(self):
        EventRegistration.objects.filter(pk=self.registration.pk).update(
            state=EventRegistration.State.CANCELLED
        )
        self.registration.refresh_from_db()
        with self.assertRaises(InvalidAttendance):
            self.record_registered()

    def test_wrong_event_or_subject_registration_is_denied(self):
        other_event = Event.objects.create(
            event_id="event:other",
            title="Other",
            owner=self.actor,
            state=Event.State.ACTIVE,
            created_at=NOW,
        )
        wrong_event = EventRegistration.objects.create(
            registration_id="registration:wrong-event",
            event=other_event,
            participant=self.subject,
            state=EventRegistration.State.REGISTERED,
            origin=EventRegistration.Origin.THIRD_PARTY,
            event_state_at_registration=Event.State.ACTIVE,
            eligibility_basis_type=(
                EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION
            ),
            eligibility_basis_reference="eligible",
            eligibility_evaluated_at=NOW,
            registered_at=NOW,
        )
        with self.assertRaises(InvalidAttendance):
            self.record_registered(supporting_registration=wrong_event)

        wrong_subject = EventRegistration.objects.create(
            registration_id="registration:wrong-subject",
            event=self.event,
            participant=self.other,
            state=EventRegistration.State.REGISTERED,
            origin=EventRegistration.Origin.THIRD_PARTY,
            event_state_at_registration=Event.State.ACTIVE,
            eligibility_basis_type=(
                EventRegistration.EligibilityBasisType.EVENT_CONFIGURATION
            ),
            eligibility_basis_reference="eligible",
            eligibility_evaluated_at=NOW,
            registered_at=NOW,
        )
        with self.assertRaises(InvalidAttendance):
            self.record_registered(supporting_registration=wrong_subject)

    def test_null_registration_never_creates_walk_in_permission(self):
        with self.assertRaises(InvalidAttendance):
            self.record_walk_in(walk_in_eligibility=None)
        self.assertFalse(EventRegistration.objects.filter(participant=self.other).exists())

    def test_correction_changes_only_bounded_facts(self):
        attendance = self.record_registered()
        transition = self.service.correct_attendance(
            identity=self.actor_user,
            attendance_id=attendance.attendance_id,
            resulting_observed_at=CORRECTED,
            resulting_supporting_registration=self.registration,
            rationale="correct observation time",
            evidence=self.evidence("evidence:correction"),
            idempotency_key="attendance:correct",
        )

        attendance.refresh_from_db()
        self.assertEqual(attendance.event, self.event)
        self.assertEqual(attendance.subject, self.subject)
        self.assertEqual(attendance.status, EventAttendance.Status.PRESENT)
        self.assertEqual(attendance.observed_at, CORRECTED)
        self.assertEqual(transition.from_status, EventAttendance.Status.PRESENT)
        self.assertEqual(transition.to_status, EventAttendance.Status.PRESENT)
        self.assertEqual(transition.sequence, 2)
        self.assertEqual(transition.basis, "bounded_fact_correction")

    def test_completed_correction_and_archived_void_use_explicit_authority(self):
        attendance = self.record_registered()
        Event.objects.filter(pk=self.event.pk).update(state=Event.State.COMPLETED)
        correction = self.service.correct_attendance(
            identity=self.actor_user,
            attendance_id=attendance.attendance_id,
            resulting_observed_at=CORRECTED,
            resulting_supporting_registration=self.registration,
            rationale="post-completion correction",
            evidence=self.evidence("evidence:post-completion-correction"),
            idempotency_key="attendance:post-completion-correction",
        )
        self.assertEqual(self.capability.targets[-1].action, "correct_attendance")
        self.assertEqual(correction.basis, "bounded_fact_correction")

        Event.objects.filter(pk=self.event.pk).update(state=Event.State.ARCHIVED)
        void = self.service.void_attendance(
            identity=self.actor_user,
            attendance_id=attendance.attendance_id,
            rationale="historical correction",
            evidence=self.evidence("evidence:historical-correction"),
            idempotency_key="attendance:historical-correction",
        )
        self.assertEqual(
            self.capability.targets[-1].action,
            "historical_void_attendance",
        )
        self.assertEqual(void.basis, "authorised_assertion_void")

    def test_void_and_reinstate_preserve_attendance_facts(self):
        attendance = self.record_registered()
        original = (
            attendance.event_id,
            attendance.subject_id,
            attendance.observed_at,
            attendance.supporting_registration_id,
            attendance.origin,
        )
        void = self.service.void_attendance(
            identity=self.actor_user,
            attendance_id=attendance.attendance_id,
            rationale="assertion withdrawn",
            evidence=self.evidence("evidence:void"),
            idempotency_key="attendance:void",
        )
        attendance.refresh_from_db()
        self.assertEqual(attendance.status, EventAttendance.Status.VOIDED)
        self.assertEqual(void.sequence, 2)
        self.assertEqual(
            (
                attendance.event_id,
                attendance.subject_id,
                attendance.observed_at,
                attendance.supporting_registration_id,
                attendance.origin,
            ),
            original,
        )

        reinstate = self.service.reinstate_attendance(
            identity=self.actor_user,
            attendance_id=attendance.attendance_id,
            rationale="assertion restored",
            evidence=self.evidence("evidence:reinstate"),
            idempotency_key="attendance:reinstate",
        )
        attendance.refresh_from_db()
        self.assertEqual(attendance.status, EventAttendance.Status.PRESENT)
        self.assertEqual(reinstate.sequence, 3)
        self.assertEqual(reinstate.previous_transition, void)

    def test_later_registration_cancellation_does_not_erase_attendance(self):
        attendance = self.record_registered()
        EventRegistration.objects.filter(pk=self.registration.pk).update(
            state=EventRegistration.State.CANCELLED
        )

        transition = self.service.void_attendance(
            identity=self.actor_user,
            attendance_id=attendance.attendance_id,
            rationale="historical assertion withdrawn",
            evidence=self.evidence("evidence:historical-void"),
            idempotency_key="attendance:historical-void",
        )

        attendance.refresh_from_db()
        self.assertEqual(attendance.status, EventAttendance.Status.VOIDED)
        self.assertEqual(
            attendance.supporting_registration_id,
            self.registration.pk,
        )
        self.assertEqual(
            transition.resulting_supporting_registration_id,
            self.registration.pk,
        )

    def test_invalid_lifecycle_actions_are_rejected(self):
        attendance = self.record_registered()
        with self.assertRaises(InvalidAttendanceTransition):
            self.service.reinstate_attendance(
                identity=self.actor_user,
                attendance_id=attendance.attendance_id,
                rationale="invalid",
                evidence=self.evidence("evidence:invalid-reinstate"),
                idempotency_key="attendance:invalid-reinstate",
            )
        self.service.void_attendance(
            identity=self.actor_user,
            attendance_id=attendance.attendance_id,
            rationale="void",
            evidence=self.evidence("evidence:void"),
            idempotency_key="attendance:void",
        )
        with self.assertRaises(InvalidAttendanceTransition):
            self.service.correct_attendance(
                identity=self.actor_user,
                attendance_id=attendance.attendance_id,
                resulting_observed_at=CORRECTED,
                resulting_supporting_registration=self.registration,
                rationale="invalid correction",
                evidence=self.evidence("evidence:invalid-correct"),
                idempotency_key="attendance:invalid-correct",
            )

    def test_sequential_idempotency_replays_each_command(self):
        attendance = self.record_registered()
        self.assertEqual(self.record_registered().pk, attendance.pk)

        correct_kwargs = {
            "identity": self.actor_user,
            "attendance_id": attendance.attendance_id,
            "resulting_observed_at": CORRECTED,
            "resulting_supporting_registration": self.registration,
            "rationale": "correct observation time",
            "evidence": self.evidence("evidence:correction"),
            "idempotency_key": "attendance:correct",
        }
        first = self.service.correct_attendance(**correct_kwargs)
        second = self.service.correct_attendance(**correct_kwargs)
        self.assertEqual(first.pk, second.pk)

        void_kwargs = {
            "identity": self.actor_user,
            "attendance_id": attendance.attendance_id,
            "rationale": "void",
            "evidence": self.evidence("evidence:void"),
            "idempotency_key": "attendance:void",
        }
        first = self.service.void_attendance(**void_kwargs)
        second = self.service.void_attendance(**void_kwargs)
        self.assertEqual(first.pk, second.pk)

    def test_changed_payload_conflicts(self):
        self.record_registered()
        with self.assertRaises(AttendanceIdempotencyConflict):
            self.record_registered(
                evidence=self.evidence("evidence:changed"),
            )

    def test_different_actor_duplicate_is_not_replay(self):
        self.record_registered()
        other_service = EventAttendanceService(
            authority=EventAttendanceAuthority(Capability()),
            clock=lambda: NOW,
        )
        with self.assertRaises(DuplicateEventAttendance):
            other_service.record_attendance(
                identity=self.other_user,
                attendance_id="attendance:other-actor",
                event_id=self.event.event_id,
                subject=self.subject,
                origin=EventAttendance.Origin.REGISTERED,
                supporting_registration=self.registration,
                evidence=self.evidence("evidence:other-actor"),
                idempotency_key="attendance:record:registered",
            )

    def test_aggregate_transition_evidence_and_receipt_reject_mutation(self):
        attendance = self.record_walk_in()
        transition = attendance.transitions.get()
        evidence = transition.evidence_references.get()
        receipt = attendance.walk_in_eligibility_receipt

        attendance.subject = self.subject
        with self.assertRaises(ValidationError):
            attendance.save()
        with self.assertRaises(ValidationError):
            attendance.delete()
        transition.rationale = "rewrite"
        with self.assertRaises(ValidationError):
            transition.save()
        with self.assertRaises(ValidationError):
            transition.delete()
        evidence.provenance = "rewrite"
        with self.assertRaises(ValidationError):
            evidence.save()
        with self.assertRaises(ValidationError):
            evidence.delete()
        receipt.basis_reference = "rewrite"
        with self.assertRaises(ValidationError):
            receipt.save()
        with self.assertRaises(ValidationError):
            receipt.delete()

    def test_event_participation_is_not_attendance_source_or_write_target(self):
        EventParticipation.objects.bulk_create(
            [
                EventParticipation(
                    event=self.event,
                    participant=self.other,
                    attached_by=self.actor,
                    authority_reference="authority:historical-participation",
                    attached_at=NOW,
                )
            ]
        )
        self.assertFalse(
            EventAttendance.objects.filter(
                event=self.event,
                subject=self.other,
            ).exists()
        )
        self.record_registered()
        self.assertEqual(EventParticipation.objects.count(), 1)

    def test_subject_reads_present_attendance_with_allowlisted_evidence(self):
        self.record_registered()

        inspection = EventReadService.inspect_attendance(
            self.subject,
            self.event.event_id,
        )
        self.assertEqual(inspection.status, EventAttendance.Status.PRESENT)
        self.assertEqual(inspection.attendance_id, "attendance:registered")
        self.assertEqual(len(inspection.transitions), 1)
        evidence = inspection.transitions[0].evidence[0]
        self.assertEqual(evidence.evidence_type, "actor_assertion")
        self.assertEqual(
            evidence.classification,
            EventAttendanceEvidenceReference.Classification.ACTOR_ATTESTATION,
        )
        self.assertFalse(hasattr(evidence, "reference"))
        self.assertFalse(hasattr(evidence, "provenance"))
        self.assertFalse(hasattr(inspection.transitions[0], "authority_reference"))

    def test_no_row_projects_unrecorded_never_absence(self):
        inspection = EventReadService.inspect_attendance(
            self.subject,
            self.event.event_id,
        )
        self.assertEqual(inspection.status, "unrecorded")
        self.assertIsNone(inspection.attendance_id)
        self.assertEqual(inspection.transitions, ())
        self.assertNotIn(inspection.status, {"absent", "no-show", "failed to attend"})

    def test_event_detail_projects_only_viewers_attendance_truth(self):
        self.record_registered()
        subject_view = EventReadService.inspect_event(
            self.subject,
            self.event.event_id,
        )
        owner_view = EventReadService.inspect_event(
            self.actor,
            self.event.event_id,
        )

        self.assertEqual(subject_view.attendance.status, EventAttendance.Status.PRESENT)
        self.assertEqual(owner_view.attendance.status, "unrecorded")
        self.assertIsNone(owner_view.attendance.attendance_id)

    def test_cross_subject_read_is_default_denied_even_for_staff(self):
        self.record_registered()
        outsider_user = User.objects.create_user(
            username="attendance-outsider",
            is_staff=True,
            is_superuser=True,
        )
        outsider = Identity.objects.create(
            credential=outsider_user,
            access_state=Identity.AccessState.ACTIVE,
        )
        with self.assertRaises(EventNotVisible):
            EventReadService.inspect_attendance(
                outsider,
                self.event.event_id,
            )

    def test_event_participation_visibility_still_projects_unrecorded(self):
        EventParticipation.objects.bulk_create(
            [
                EventParticipation(
                    event=self.event,
                    participant=self.other,
                    attached_by=self.actor,
                    authority_reference="authority:historical-participation",
                    attached_at=NOW,
                )
            ]
        )
        inspection = EventReadService.inspect_attendance(
            self.other,
            self.event.event_id,
        )
        self.assertEqual(inspection.status, "unrecorded")
