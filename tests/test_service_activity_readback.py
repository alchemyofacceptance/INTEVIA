"""Guardians for S012 readback: visibility, DTO allowlist, lineage validation, and neutral state."""

from dataclasses import fields as dc_fields
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import (
    Identity,
    Service,
    ServiceActivity,
    ServiceActivityAssignment,
    ServiceActivityEvidenceReference,
    ServiceActivityReview,
    ServiceActivityTransition,
    ServiceVersion,
    ServiceWorkSubmission,
)
from src.intevia.services.service_authority import (
    ServiceCommandAuthority,
    ServiceCommandAuthorityResponse,
)
from src.intevia.services.service_activity_service import (
    AcceptServiceAssignmentCommand,
    AssignServiceActivityCommand,
    CancelServiceActivityCommand,
    CompleteServiceActivityCommand,
    CreateServiceActivityCommand,
    DeclineServiceAssignmentCommand,
    InitiatingDomain,
    ReviewServiceWorkCommand,
    ServiceActivityService,
    ServiceActivityState,
    SubmitServiceWorkCommand,
)
from src.intevia.services.service_activity_read_service import (
    ServiceActivityAssignmentDTO,
    ServiceActivityEvidenceDTO,
    ServiceActivityHistoryEntryDTO,
    ServiceActivityReadDTO,
    ServiceActivityReadError,
    ServiceActivityReadLineageError,
    ServiceActivityReadNotAuthorised,
    ServiceActivityReadNotFound,
    ServiceActivityReadService,
    ServiceActivityReviewDTO,
    ServiceActivityVisibilityRequest,
    ServiceActivityVisibilityResponse,
    ServiceWorkSubmissionDTO,
    _STATE_MESSAGES,
)


NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 26, 13, 0, 0, tzinfo=timezone.utc)


class _TestProvider:
    def authorise(self, *, request):
        return ServiceCommandAuthorityResponse(
            database_alias=request.database_alias,
            actor_pk=request.actor_pk,
            actor_identity_id=request.actor_identity_id,
            actor_access_epoch=request.actor_access_epoch,
            action=request.action,
            target_fingerprint=request.target_fingerprint,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            evaluated_at=request.evaluated_at,
            authority_reference="AUTH-S012-TEST",
        )


class _GrantingVisibilityProvider:
    def check_visibility(self, *, request):
        return ServiceActivityVisibilityResponse(
            database_alias=request.database_alias,
            viewer_identity_id=request.viewer_identity_id,
            viewer_access_epoch=request.viewer_access_epoch,
            activity_id=request.activity_id,
            evaluated_at=request.evaluated_at,
            visible=True,
            authority_reference="AUTH-VIS-TEST",
        )


class _DenyingVisibilityProvider:
    def check_visibility(self, *, request):
        return ServiceActivityVisibilityResponse(
            database_alias=request.database_alias,
            viewer_identity_id=request.viewer_identity_id,
            viewer_access_epoch=request.viewer_access_epoch,
            activity_id=request.activity_id,
            evaluated_at=request.evaluated_at,
            visible=False,
            authority_reference="AUTH-VIS-DENY",
        )


class _NoneVisibilityProvider:
    def check_visibility(self, *, request):
        return None


class _MismatchVisibilityProvider:
    def check_visibility(self, *, request):
        return ServiceActivityVisibilityResponse(
            database_alias="wrong_db",
            viewer_identity_id=request.viewer_identity_id,
            viewer_access_epoch=request.viewer_access_epoch,
            activity_id=request.activity_id,
            evaluated_at=request.evaluated_at,
            visible=True,
            authority_reference="AUTH-VIS-MISMATCH",
        )


def _make_identity(username, *, active=True):
    user = User.objects.create_user(username=username, password="test-pass")
    if active:
        user.is_active = True
        user.save()
    identity = Identity.objects.create(
        credential=user,
        access_state=Identity.AccessState.ACTIVE if active else Identity.AccessState.PENDING,
    )
    return identity


def _make_service(creator):
    service = Service(
        service_id=f"svc-{uuid4().hex[:8]}",
        state=Service.State.PUBLISHED,
        created_by=creator,
        created_at=NOW,
    )
    service.save()
    sv = ServiceVersion(
        service=service,
        version_number=1,
        capability_purpose="Test",
        domain_intent="Test",
        created_by=creator,
        created_at=NOW,
    )
    sv.save()
    service.current_version = sv
    service.save()
    return service, sv


def _make_write_service(creator):
    service, sv = _make_service(creator)
    authority = ServiceCommandAuthority(provider=_TestProvider())
    svc = ServiceActivityService(authority=authority, clock=lambda: NOW)
    return service, sv, svc


def _make_read_service(*, visibility_provider=None):
    if visibility_provider is None:
        visibility_provider = _GrantingVisibilityProvider()
    return ServiceActivityReadService(
        visibility_provider=visibility_provider,
        clock=lambda: NOW,
    )


def _create_and_complete(svc, creator, assignee, reviewer, sv):
    """Drive an activity through the full lifecycle to COMPLETED."""
    aid = uuid4()
    svc.create_service_activity(CreateServiceActivityCommand(
        credential=creator.credential,
        request_reference="REQ-C", idempotency_key=f"IDEM-C-{uuid4().hex[:8]}",
        occurred_at=NOW, activity_id=aid, service_version_pk=sv.pk,
        initiating_domain=InitiatingDomain.SERVICE,
        initiating_domain_reference="REF-INIT",
        activity_basis_reference="REF-BASIS",
    ))
    svc.assign_service_activity(AssignServiceActivityCommand(
        credential=creator.credential,
        request_reference="REQ-A", idempotency_key=f"IDEM-A-{uuid4().hex[:8]}",
        occurred_at=LATER, activity_id=aid,
        assignee_identity_id=assignee.identity_id,
        assignment_reference="REF-A", assignment_basis_reference="REF-AB",
    ))
    svc.accept_service_assignment(AcceptServiceAssignmentCommand(
        credential=assignee.credential,
        request_reference="REQ-ACC", idempotency_key=f"IDEM-ACC-{uuid4().hex[:8]}",
        occurred_at=LATER, activity_id=aid,
    ))
    svc.submit_service_work(SubmitServiceWorkCommand(
        credential=assignee.credential,
        request_reference="REQ-S", idempotency_key=f"IDEM-S-{uuid4().hex[:8]}",
        occurred_at=LATER, activity_id=aid,
        submission_reference="REF-SUB",
        submission_support_references=("REF-SUPPORT",),
    ))
    svc.review_service_work(ReviewServiceWorkCommand(
        credential=reviewer.credential,
        request_reference="REQ-R", idempotency_key=f"IDEM-R-{uuid4().hex[:8]}",
        occurred_at=LATER, activity_id=aid,
        review_reference="REF-REV", review_record_reference="REF-REC",
    ))
    svc.complete_service_activity(CompleteServiceActivityCommand(
        credential=creator.credential,
        request_reference="REQ-CMP", idempotency_key=f"IDEM-CMP-{uuid4().hex[:8]}",
        occurred_at=LATER, activity_id=aid,
        completion_record_reference="REF-COMP",
    ))
    return aid


class CreatorVisibilityTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("vis-creator")
        self.service, self.sv, self.write_svc = _make_write_service(self.creator)
        result = self.write_svc.create_service_activity(CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ", idempotency_key="IDEM-VIS",
            occurred_at=NOW, activity_id=uuid4(), service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.SERVICE,
            initiating_domain_reference="REF",
            activity_basis_reference="REF-B",
        ))
        self.activity_id = result.activity_id

    def test_creator_can_read_without_provider(self):
        read_svc = _make_read_service(visibility_provider=_NoneVisibilityProvider())
        dto = read_svc.read_service_activity(
            credential=self.creator.credential,
            activity_id=self.activity_id,
        )
        self.assertIsInstance(dto, ServiceActivityReadDTO)
        self.assertEqual(dto.activity_id, self.activity_id)


class AssigneeVisibilityTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("avis-creator")
        self.assignee = _make_identity("avis-assignee")
        self.service, self.sv, self.write_svc = _make_write_service(self.creator)
        result = self.write_svc.create_service_activity(CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ", idempotency_key="IDEM-AVIS",
            occurred_at=NOW, activity_id=uuid4(), service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.SERVICE,
            initiating_domain_reference="REF",
            activity_basis_reference="REF-B",
        ))
        self.activity_id = result.activity_id
        self.write_svc.assign_service_activity(AssignServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-A", idempotency_key="IDEM-AVIS-A",
            occurred_at=LATER, activity_id=self.activity_id,
            assignee_identity_id=self.assignee.identity_id,
            assignment_reference="REF-A", assignment_basis_reference="REF-AB",
        ))

    def test_assignee_can_read_without_provider(self):
        read_svc = _make_read_service(visibility_provider=_NoneVisibilityProvider())
        dto = read_svc.read_service_activity(
            credential=self.assignee.credential,
            activity_id=self.activity_id,
        )
        self.assertEqual(dto.activity_id, self.activity_id)


class ThirdPartyVisibilityTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("tp-creator")
        self.third_party = _make_identity("tp-viewer")
        self.service, self.sv, self.write_svc = _make_write_service(self.creator)
        result = self.write_svc.create_service_activity(CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ", idempotency_key="IDEM-TP",
            occurred_at=NOW, activity_id=uuid4(), service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.SERVICE,
            initiating_domain_reference="REF",
            activity_basis_reference="REF-B",
        ))
        self.activity_id = result.activity_id

    def test_third_party_requires_provider_visible_true(self):
        read_svc = _make_read_service(visibility_provider=_GrantingVisibilityProvider())
        dto = read_svc.read_service_activity(
            credential=self.third_party.credential,
            activity_id=self.activity_id,
        )
        self.assertEqual(dto.activity_id, self.activity_id)

    def test_third_party_denied_when_visible_false(self):
        read_svc = _make_read_service(visibility_provider=_DenyingVisibilityProvider())
        with self.assertRaises(ServiceActivityReadNotAuthorised):
            read_svc.read_service_activity(
                credential=self.third_party.credential,
                activity_id=self.activity_id,
            )

    def test_third_party_denied_when_provider_returns_none(self):
        read_svc = _make_read_service(visibility_provider=_NoneVisibilityProvider())
        with self.assertRaises(ServiceActivityReadNotAuthorised):
            read_svc.read_service_activity(
                credential=self.third_party.credential,
                activity_id=self.activity_id,
            )

    def test_third_party_denied_on_response_mismatch(self):
        read_svc = _make_read_service(visibility_provider=_MismatchVisibilityProvider())
        with self.assertRaises(ServiceActivityReadNotAuthorised):
            read_svc.read_service_activity(
                credential=self.third_party.credential,
                activity_id=self.activity_id,
            )

    def test_inactive_viewer_denied(self):
        inactive = _make_identity("inactive-viewer", active=False)
        read_svc = _make_read_service()
        with self.assertRaises(ServiceActivityReadNotAuthorised):
            read_svc.read_service_activity(
                credential=inactive.credential,
                activity_id=self.activity_id,
            )

    def test_nonexistent_activity_not_found(self):
        read_svc = _make_read_service()
        with self.assertRaises(ServiceActivityReadNotFound):
            read_svc.read_service_activity(
                credential=self.creator.credential,
                activity_id=uuid4(),
            )


class DTOAllowlistTests(TestCase):
    def test_read_dto_exact_field_set(self):
        expected = [
            "activity_id", "service_id", "service_version_id",
            "service_state", "service_version_is_current",
            "initiating_domain", "initiating_domain_reference",
            "state", "state_message",
            "assignment", "work_submission", "review", "history",
        ]
        actual = [f.name for f in dc_fields(ServiceActivityReadDTO)]
        self.assertEqual(actual, expected)

    def test_history_entry_dto_exact_field_set(self):
        expected = [
            "sequence", "action", "from_state", "to_state",
            "actor_identity_id", "occurred_at", "lineage_reference",
            "evidence",
        ]
        actual = [f.name for f in dc_fields(ServiceActivityHistoryEntryDTO)]
        self.assertEqual(actual, expected)

    def test_evidence_dto_exact_field_set(self):
        expected = ["evidence_kind", "reference"]
        actual = [f.name for f in dc_fields(ServiceActivityEvidenceDTO)]
        self.assertEqual(actual, expected)

    def test_assignment_dto_exact_field_set(self):
        expected = [
            "assignee_identity_id", "assigned_by_identity_id",
            "assignment_reference", "assigned_at",
        ]
        actual = [f.name for f in dc_fields(ServiceActivityAssignmentDTO)]
        self.assertEqual(actual, expected)

    def test_submission_dto_exact_field_set(self):
        expected = [
            "submitted_by_identity_id", "submission_reference",
            "submitted_at",
        ]
        actual = [f.name for f in dc_fields(ServiceWorkSubmissionDTO)]
        self.assertEqual(actual, expected)

    def test_review_dto_exact_field_set(self):
        expected = [
            "reviewed_by_identity_id", "review_reference",
            "reviewed_at",
        ]
        actual = [f.name for f in dc_fields(ServiceActivityReviewDTO)]
        self.assertEqual(actual, expected)


class StateMessageTests(TestCase):
    def test_eight_state_messages_exist(self):
        self.assertEqual(len(_STATE_MESSAGES), 8)

    def test_each_state_has_a_message(self):
        for state_val, _ in ServiceActivityState.choices:
            self.assertIn(state_val, _STATE_MESSAGES,
                          f"missing message for {state_val}")

    def test_unassigned_message_exact(self):
        self.assertIn("No judgement", _STATE_MESSAGES["unassigned"])

    def test_completed_message_exact(self):
        self.assertIn(
            "does not record whether the work was accepted",
            _STATE_MESSAGES["completed"],
        )

    def test_declined_message_exact(self):
        self.assertIn(
            "does not imply fault",
            _STATE_MESSAGES["declined"],
        )

    def test_cancelled_message_exact(self):
        self.assertIn(
            "earlier work was correct",
            _STATE_MESSAGES["cancelled"],
        )


class FullReadbackDTOTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("rb-creator")
        self.assignee = _make_identity("rb-assignee")
        self.reviewer = _make_identity("rb-reviewer")
        self.service, self.sv, self.write_svc = _make_write_service(self.creator)
        self.aid = _create_and_complete(
            self.write_svc, self.creator, self.assignee, self.reviewer, self.sv,
        )
        self.read_svc = _make_read_service()

    def test_completed_activity_dto(self):
        dto = self.read_svc.read_service_activity(
            credential=self.creator.credential,
            activity_id=self.aid,
        )
        self.assertEqual(dto.state, ServiceActivityState.COMPLETED)
        self.assertIn("does not record", dto.state_message)
        self.assertIsNotNone(dto.assignment)
        self.assertIsNotNone(dto.work_submission)
        self.assertIsNotNone(dto.review)
        self.assertEqual(len(dto.history), 6)  # CREATE..COMPLETE = 6 transitions
        # History is ordered by sequence
        for i, entry in enumerate(dto.history):
            self.assertEqual(entry.sequence, i + 1)
        # First entry is CREATE with null from_state
        self.assertIsNone(dto.history[0].from_state)
        # Last entry is COMPLETE
        self.assertEqual(dto.history[-1].to_state, ServiceActivityState.COMPLETED)

    def test_dto_excludes_authority_internals(self):
        dto = self.read_svc.read_service_activity(
            credential=self.creator.credential,
            activity_id=self.aid,
        )
        field_names = {f.name for f in dc_fields(dto)}
        prohibited = {
            "authority_reference", "authority_decision_reference",
            "authority_evaluated_at", "actor_access_epoch",
            "payload_fingerprint", "request_reference",
            "idempotency_key", "credential",
        }
        self.assertEqual(field_names & prohibited, set())

    def test_unassigned_activity_dto_children_absent(self):
        result = self.write_svc.create_service_activity(CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ", idempotency_key="IDEM-UADTO",
            occurred_at=NOW, activity_id=uuid4(), service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.SERVICE,
            initiating_domain_reference="REF",
            activity_basis_reference="REF-B",
        ))
        dto = self.read_svc.read_service_activity(
            credential=self.creator.credential,
            activity_id=result.activity_id,
        )
        self.assertIsNone(dto.assignment)
        self.assertIsNone(dto.work_submission)
        self.assertIsNone(dto.review)
        self.assertEqual(len(dto.history), 1)


class RetiredParentReadbackTests(TestCase):
    def test_retired_parent_readback_neutral(self):
        creator = _make_identity("retired-rb-creator")
        assignee = _make_identity("retired-rb-assignee")
        reviewer = _make_identity("retired-rb-reviewer")
        service, sv, write_svc = _make_write_service(creator)
        aid = _create_and_complete(write_svc, creator, assignee, reviewer, sv)

        # Retire the service
        service.state = Service.State.RETIRED
        service.save()

        read_svc = _make_read_service()
        dto = read_svc.read_service_activity(
            credential=creator.credential,
            activity_id=aid,
        )
        self.assertEqual(dto.state, ServiceActivityState.COMPLETED)
        self.assertEqual(dto.service_state, Service.State.RETIRED)
        # current_version FK is unchanged by retirement; version_is_current stays True
        self.assertTrue(dto.service_version_is_current)

    def test_successor_version_readback_shows_not_current(self):
        creator = _make_identity("succ-rb-creator")
        assignee = _make_identity("succ-rb-assignee")
        reviewer = _make_identity("succ-rb-reviewer")
        service, sv, write_svc = _make_write_service(creator)
        aid = _create_and_complete(write_svc, creator, assignee, reviewer, sv)

        # Create successor version
        sv2 = ServiceVersion(
            service=service, version_number=2,
            capability_purpose="V2", domain_intent="V2",
            created_by=creator, created_at=NOW,
            predecessor=sv,
        )
        sv2.save()
        service.current_version = sv2
        service.save()

        read_svc = _make_read_service()
        dto = read_svc.read_service_activity(
            credential=creator.credential,
            activity_id=aid,
        )
        self.assertFalse(dto.service_version_is_current)
        self.assertEqual(dto.service_version_id, sv.pk)


class LineageValidationFailureTests(TestCase):
    """Readback fails closed on corrupted lineage."""

    def setUp(self):
        self.creator = _make_identity("lineage-fail-creator")
        self.service, self.sv, self.write_svc = _make_write_service(self.creator)
        result = self.write_svc.create_service_activity(CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ", idempotency_key="IDEM-LF",
            occurred_at=NOW, activity_id=uuid4(), service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.SERVICE,
            initiating_domain_reference="REF",
            activity_basis_reference="REF-B",
        ))
        self.activity_id = result.activity_id

    def test_digest_mismatch_fails_closed(self):
        """Corrupt the stored payload_fingerprint; readback must detect it."""
        trans = ServiceActivityTransition.objects.get(
            activity__activity_id=self.activity_id
        )
        # Bypass ORM immutability to corrupt data
        ServiceActivityTransition.objects.filter(pk=trans.pk).update(
            payload_fingerprint="0" * 64
        )
        read_svc = _make_read_service()
        with self.assertRaises(ServiceActivityReadLineageError):
            read_svc.read_service_activity(
                credential=self.creator.credential,
                activity_id=self.activity_id,
            )

    def test_evidence_time_mismatch_fails_closed(self):
        """Corrupt evidence occurred_at; readback must detect it."""
        trans = ServiceActivityTransition.objects.get(
            activity__activity_id=self.activity_id
        )
        # Bypass ORM immutability to corrupt evidence time
        ServiceActivityEvidenceReference.objects.filter(
            transition=trans
        ).update(occurred_at=NOW + timedelta(hours=5))
        read_svc = _make_read_service()
        with self.assertRaises(ServiceActivityReadLineageError):
            read_svc.read_service_activity(
                credential=self.creator.credential,
                activity_id=self.activity_id,
            )

    def test_lineage_reference_mismatch_fails_closed(self):
        """Corrupt the stored lineage_reference; readback must detect it."""
        trans = ServiceActivityTransition.objects.get(
            activity__activity_id=self.activity_id
        )
        ServiceActivityTransition.objects.filter(pk=trans.pk).update(
            lineage_reference="s012l1:" + "0" * 64
        )
        read_svc = _make_read_service()
        with self.assertRaises(ServiceActivityReadLineageError):
            read_svc.read_service_activity(
                credential=self.creator.credential,
                activity_id=self.activity_id,
            )
