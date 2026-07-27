"""Guardians for S012 SERVICE-owned command service: all 8 commands, lifecycle, replay, and conflicts."""

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
    ServiceCommandAuthorityRequest,
    ServiceCommandAuthorityResponse,
    ServiceCommandNotAuthorised,
)
from src.intevia.services.service_activity_service import (
    AcceptServiceAssignmentCommand,
    AcceptServiceAssignmentResult,
    AssignServiceActivityCommand,
    AssignServiceActivityResult,
    CancelServiceActivityCommand,
    CancelServiceActivityResult,
    CompleteServiceActivityCommand,
    CompleteServiceActivityResult,
    CreateServiceActivityCommand,
    CreateServiceActivityResult,
    DeclineServiceAssignmentCommand,
    DeclineServiceAssignmentResult,
    InitiatingDomain,
    ReviewServiceWorkCommand,
    ReviewServiceWorkResult,
    ServiceActivityActorError,
    ServiceActivityCommandError,
    ServiceActivityConflict,
    ServiceActivityCrossEpochConflict,
    ServiceActivityLifecycleError,
    ServiceActivityNotFound,
    ServiceActivityPayloadConflict,
    ServiceActivityService,
    ServiceActivityState,
    ServiceActivityValidationError,
    ServiceCommandAction,
    SubmitServiceWorkCommand,
    SubmitServiceWorkResult,
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


class _RefusingProvider:
    def authorise(self, *, request):
        return None


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
        capability_purpose="Test capability",
        domain_intent="Test domain intent",
        created_by=creator,
        created_at=NOW,
    )
    sv.save()
    service.current_version = sv
    service.save()
    return service, sv


def _make_service_and_svc(creator):
    service, sv = _make_service(creator)
    authority = ServiceCommandAuthority(provider=_TestProvider())
    svc = ServiceActivityService(
        authority=authority,
        clock=lambda: NOW,
    )
    return service, sv, svc


def _create_activity(svc, credential, sv, **overrides):
    defaults = dict(
        credential=credential,
        request_reference="REQ-CREATE",
        idempotency_key=f"IDEM-{uuid4().hex[:12]}",
        occurred_at=NOW,
        activity_id=uuid4(),
        service_version_pk=sv.pk,
        initiating_domain=InitiatingDomain.SERVICE,
        initiating_domain_reference="REF-INIT",
        activity_basis_reference="REF-BASIS",
    )
    defaults.update(overrides)
    return svc.create_service_activity(CreateServiceActivityCommand(**defaults))


class CreateServiceActivityTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("create-test")
        self.service, self.sv, self.svc = _make_service_and_svc(self.creator)

    def test_create_returns_correct_result_type(self):
        result = _create_activity(self.svc, self.creator.credential, self.sv)
        self.assertIsInstance(result, CreateServiceActivityResult)
        self.assertEqual(result.resulting_state, ServiceActivityState.UNASSIGNED)
        self.assertEqual(result.transition_sequence, 1)
        self.assertTrue(result.lineage_reference.startswith("s012l1:"))
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0][0].value, "activity_basis")

    def test_create_stores_exact_occurrence_time(self):
        aid = uuid4()
        result = _create_activity(self.svc, self.creator.credential, self.sv, activity_id=aid)
        activity = ServiceActivity.objects.get(activity_id=aid)
        trans = ServiceActivityTransition.objects.get(pk=result.transition_id)
        self.assertEqual(activity.created_at, NOW)
        self.assertEqual(trans.occurred_at, NOW)
        evidence = ServiceActivityEvidenceReference.objects.filter(transition=trans)
        for ev in evidence:
            self.assertEqual(ev.occurred_at, NOW)

    def test_create_requires_published_service(self):
        self.service.state = Service.State.DRAFT
        self.service.save()
        with self.assertRaises(ServiceActivityLifecycleError):
            _create_activity(self.svc, self.creator.credential, self.sv)

    def test_create_requires_current_version(self):
        sv2 = ServiceVersion(
            service=self.service, version_number=2,
            capability_purpose="V2", domain_intent="V2",
            created_by=self.creator, created_at=NOW,
            predecessor=self.sv,
        )
        sv2.save()
        self.service.current_version = sv2
        self.service.save()
        with self.assertRaises(ServiceActivityLifecycleError):
            _create_activity(self.svc, self.creator.credential, self.sv)

    def test_create_invalid_version_pk(self):
        with self.assertRaises(ServiceActivityValidationError):
            _create_activity(self.svc, self.creator.credential, self.sv,
                             service_version_pk=0)

    def test_create_nonexistent_version(self):
        with self.assertRaises(ServiceActivityNotFound):
            _create_activity(self.svc, self.creator.credential, self.sv,
                             service_version_pk=99999)

    def test_create_inactive_actor_refused(self):
        inactive = _make_identity("inactive-create", active=False)
        with self.assertRaises(ServiceActivityActorError):
            _create_activity(self.svc, inactive.credential, self.sv)

    def test_create_supplied_by_equals_actor(self):
        result = _create_activity(self.svc, self.creator.credential, self.sv)
        evidence = ServiceActivityEvidenceReference.objects.filter(
            transition_id=result.transition_id
        )
        for ev in evidence:
            self.assertEqual(ev.supplied_by_id, self.creator.pk)

    def test_create_caller_supplied_activity_id(self):
        aid = uuid4()
        result = _create_activity(self.svc, self.creator.credential, self.sv, activity_id=aid)
        self.assertEqual(result.activity_id, aid)
        activity = ServiceActivity.objects.get(activity_id=aid)
        self.assertEqual(activity.activity_id, aid)


class AssignServiceActivityTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("assign-creator")
        self.assignee = _make_identity("assign-assignee")
        self.service, self.sv, self.svc = _make_service_and_svc(self.creator)
        result = _create_activity(self.svc, self.creator.credential, self.sv)
        self.activity_id = result.activity_id

    def test_assign_creates_assignment_and_evidence(self):
        result = self.svc.assign_service_activity(AssignServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-ASSIGN",
            idempotency_key="IDEM-ASSIGN",
            occurred_at=LATER,
            activity_id=self.activity_id,
            assignee_identity_id=self.assignee.identity_id,
            assignment_reference="REF-ASSIGN",
            assignment_basis_reference="REF-ASSIGN-BASIS",
        ))
        self.assertIsInstance(result, AssignServiceActivityResult)
        self.assertEqual(result.resulting_state, ServiceActivityState.ASSIGNED)
        self.assertIsNotNone(result.assignment_id)
        self.assertEqual(result.transition_sequence, 2)
        assignment = ServiceActivityAssignment.objects.get(pk=result.assignment_id)
        self.assertEqual(assignment.assignee_id, self.assignee.pk)
        self.assertEqual(assignment.assigned_at, LATER)

    def test_assign_inactive_assignee_refused(self):
        inactive = _make_identity("inactive-assign", active=False)
        with self.assertRaises(ServiceActivityActorError):
            self.svc.assign_service_activity(AssignServiceActivityCommand(
                credential=self.creator.credential,
                request_reference="REQ", idempotency_key="IDEM-BADASSIGN",
                occurred_at=LATER, activity_id=self.activity_id,
                assignee_identity_id=inactive.identity_id,
                assignment_reference="REF", assignment_basis_reference="REF",
            ))

    def test_assign_credential_inactive_assignee_refused(self):
        cred_inactive = _make_identity("cred-inactive-assign")
        cred_inactive.credential.is_active = False
        cred_inactive.credential.save()
        with self.assertRaises(ServiceActivityActorError):
            self.svc.assign_service_activity(AssignServiceActivityCommand(
                credential=self.creator.credential,
                request_reference="REQ", idempotency_key="IDEM-CREDINACTIVE",
                occurred_at=LATER, activity_id=self.activity_id,
                assignee_identity_id=cred_inactive.identity_id,
                assignment_reference="REF", assignment_basis_reference="REF",
            ))


class AcceptDeclineTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("accept-creator")
        self.assignee = _make_identity("accept-assignee")
        self.service, self.sv, self.svc = _make_service_and_svc(self.creator)
        result = _create_activity(self.svc, self.creator.credential, self.sv)
        self.activity_id = result.activity_id
        self.svc.assign_service_activity(AssignServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-ASSIGN", idempotency_key="IDEM-ASSIGN",
            occurred_at=LATER, activity_id=self.activity_id,
            assignee_identity_id=self.assignee.identity_id,
            assignment_reference="REF-A", assignment_basis_reference="REF-AB",
        ))

    def test_accept_by_assignee(self):
        result = self.svc.accept_service_assignment(AcceptServiceAssignmentCommand(
            credential=self.assignee.credential,
            request_reference="REQ-ACCEPT", idempotency_key="IDEM-ACCEPT",
            occurred_at=LATER, activity_id=self.activity_id,
        ))
        self.assertIsInstance(result, AcceptServiceAssignmentResult)
        self.assertEqual(result.resulting_state, ServiceActivityState.IN_PROGRESS)
        self.assertEqual(result.evidence, ())  # ACCEPT has no evidence

    def test_accept_by_wrong_actor_refused(self):
        with self.assertRaises(ServiceActivityActorError):
            self.svc.accept_service_assignment(AcceptServiceAssignmentCommand(
                credential=self.creator.credential,
                request_reference="REQ-WRONG", idempotency_key="IDEM-WRONGACCEPT",
                occurred_at=LATER, activity_id=self.activity_id,
            ))

    def test_decline_by_assignee(self):
        result = self.svc.decline_service_assignment(DeclineServiceAssignmentCommand(
            credential=self.assignee.credential,
            request_reference="REQ-DECLINE", idempotency_key="IDEM-DECLINE",
            occurred_at=LATER, activity_id=self.activity_id,
            decline_basis_reference="REF-DECLINE-BASIS",
        ))
        self.assertIsInstance(result, DeclineServiceAssignmentResult)
        self.assertEqual(result.resulting_state, ServiceActivityState.DECLINED)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0][0].value, "decline_basis")

    def test_decline_by_wrong_actor_refused(self):
        with self.assertRaises(ServiceActivityActorError):
            self.svc.decline_service_assignment(DeclineServiceAssignmentCommand(
                credential=self.creator.credential,
                request_reference="REQ-WRONG", idempotency_key="IDEM-WRONGDECLINE",
                occurred_at=LATER, activity_id=self.activity_id,
                decline_basis_reference="REF",
            ))


class SubmitReviewCompleteTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("lifecycle-creator")
        self.assignee = _make_identity("lifecycle-assignee")
        self.reviewer = _make_identity("lifecycle-reviewer")
        self.service, self.sv, self.svc = _make_service_and_svc(self.creator)
        result = _create_activity(self.svc, self.creator.credential, self.sv)
        self.activity_id = result.activity_id
        self.svc.assign_service_activity(AssignServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-A", idempotency_key="IDEM-A",
            occurred_at=LATER, activity_id=self.activity_id,
            assignee_identity_id=self.assignee.identity_id,
            assignment_reference="REF-A", assignment_basis_reference="REF-AB",
        ))
        self.svc.accept_service_assignment(AcceptServiceAssignmentCommand(
            credential=self.assignee.credential,
            request_reference="REQ-ACC", idempotency_key="IDEM-ACC",
            occurred_at=LATER, activity_id=self.activity_id,
        ))

    def test_submit_by_assignee(self):
        result = self.svc.submit_service_work(SubmitServiceWorkCommand(
            credential=self.assignee.credential,
            request_reference="REQ-SUB", idempotency_key="IDEM-SUB",
            occurred_at=LATER, activity_id=self.activity_id,
            submission_reference="REF-SUB",
            submission_support_references=("REF-SUPPORT-1", "REF-SUPPORT-2"),
        ))
        self.assertIsInstance(result, SubmitServiceWorkResult)
        self.assertEqual(result.resulting_state, ServiceActivityState.SUBMITTED)
        self.assertIsNotNone(result.work_submission_id)
        self.assertEqual(len(result.evidence), 2)

    def test_submit_by_non_assignee_refused(self):
        with self.assertRaises(ServiceActivityActorError):
            self.svc.submit_service_work(SubmitServiceWorkCommand(
                credential=self.creator.credential,
                request_reference="REQ-BAD", idempotency_key="IDEM-BADSUB",
                occurred_at=LATER, activity_id=self.activity_id,
                submission_reference="REF", submission_support_references=(),
            ))

    def test_submit_with_duplicate_support_references_refused(self):
        with self.assertRaises(ServiceActivityValidationError):
            self.svc.submit_service_work(SubmitServiceWorkCommand(
                credential=self.assignee.credential,
                request_reference="REQ-DUP", idempotency_key="IDEM-DUP",
                occurred_at=LATER, activity_id=self.activity_id,
                submission_reference="REF",
                submission_support_references=("DUP", "DUP"),
            ))

    def test_submit_with_zero_support_references(self):
        result = self.svc.submit_service_work(SubmitServiceWorkCommand(
            credential=self.assignee.credential,
            request_reference="REQ-NOSUP", idempotency_key="IDEM-NOSUP",
            occurred_at=LATER, activity_id=self.activity_id,
            submission_reference="REF-NOSUP",
            submission_support_references=(),
        ))
        self.assertEqual(result.evidence, ())

    def test_review_by_any_qualified_actor(self):
        self.svc.submit_service_work(SubmitServiceWorkCommand(
            credential=self.assignee.credential,
            request_reference="REQ-SUB", idempotency_key="IDEM-SUB2",
            occurred_at=LATER, activity_id=self.activity_id,
            submission_reference="REF-SUB", submission_support_references=(),
        ))
        result = self.svc.review_service_work(ReviewServiceWorkCommand(
            credential=self.reviewer.credential,
            request_reference="REQ-REV", idempotency_key="IDEM-REV",
            occurred_at=LATER, activity_id=self.activity_id,
            review_reference="REF-REV",
            review_record_reference="REF-RECORD",
        ))
        self.assertIsInstance(result, ReviewServiceWorkResult)
        self.assertEqual(result.resulting_state, ServiceActivityState.REVIEWED)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0][0].value, "review_record")

    def test_complete_after_review(self):
        self.svc.submit_service_work(SubmitServiceWorkCommand(
            credential=self.assignee.credential,
            request_reference="REQ-SUB", idempotency_key="IDEM-SUBLC",
            occurred_at=LATER, activity_id=self.activity_id,
            submission_reference="REF-SUB", submission_support_references=(),
        ))
        self.svc.review_service_work(ReviewServiceWorkCommand(
            credential=self.reviewer.credential,
            request_reference="REQ-REV", idempotency_key="IDEM-REVLC",
            occurred_at=LATER, activity_id=self.activity_id,
            review_reference="REF-REV", review_record_reference="REF-REC",
        ))
        completer = _make_identity("completer")
        result = self.svc.complete_service_activity(CompleteServiceActivityCommand(
            credential=completer.credential,
            request_reference="REQ-COMP", idempotency_key="IDEM-COMP",
            occurred_at=LATER, activity_id=self.activity_id,
            completion_record_reference="REF-COMPLETE",
        ))
        self.assertIsInstance(result, CompleteServiceActivityResult)
        self.assertEqual(result.resulting_state, ServiceActivityState.COMPLETED)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.evidence[0][0].value, "completion_record")


class CancellationTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("cancel-creator")
        self.assignee = _make_identity("cancel-assignee")
        self.service, self.sv, self.svc = _make_service_and_svc(self.creator)

    def _create_and_advance(self, target_state):
        result = _create_activity(self.svc, self.creator.credential, self.sv)
        aid = result.activity_id
        if target_state == "unassigned":
            return aid
        self.svc.assign_service_activity(AssignServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-A", idempotency_key=f"IDEM-A-{uuid4().hex[:8]}",
            occurred_at=LATER, activity_id=aid,
            assignee_identity_id=self.assignee.identity_id,
            assignment_reference="REF-A", assignment_basis_reference="REF-AB",
        ))
        if target_state == "assigned":
            return aid
        self.svc.accept_service_assignment(AcceptServiceAssignmentCommand(
            credential=self.assignee.credential,
            request_reference="REQ-ACC", idempotency_key=f"IDEM-ACC-{uuid4().hex[:8]}",
            occurred_at=LATER, activity_id=aid,
        ))
        if target_state == "in_progress":
            return aid
        self.svc.submit_service_work(SubmitServiceWorkCommand(
            credential=self.assignee.credential,
            request_reference="REQ-S", idempotency_key=f"IDEM-S-{uuid4().hex[:8]}",
            occurred_at=LATER, activity_id=aid,
            submission_reference="REF", submission_support_references=(),
        ))
        if target_state == "submitted":
            return aid
        reviewer = _make_identity(f"cancel-rev-{uuid4().hex[:8]}")
        self.svc.review_service_work(ReviewServiceWorkCommand(
            credential=reviewer.credential,
            request_reference="REQ-R", idempotency_key=f"IDEM-R-{uuid4().hex[:8]}",
            occurred_at=LATER, activity_id=aid,
            review_reference="REF-R", review_record_reference="REC-R",
        ))
        return aid

    def test_cancel_from_all_five_nonterminal_states(self):
        for state in ["unassigned", "assigned", "in_progress", "submitted", "reviewed"]:
            with self.subTest(from_state=state):
                aid = self._create_and_advance(state)
                canceller = _make_identity(f"canceller-{state}-{uuid4().hex[:4]}")
                result = self.svc.cancel_service_activity(CancelServiceActivityCommand(
                    credential=canceller.credential,
                    request_reference="REQ-CAN",
                    idempotency_key=f"IDEM-CAN-{state}-{uuid4().hex[:8]}",
                    occurred_at=LATER, activity_id=aid,
                    cancellation_basis_reference="REF-CANCEL",
                ))
                self.assertIsInstance(result, CancelServiceActivityResult)
                self.assertEqual(result.resulting_state, ServiceActivityState.CANCELLED)


class TerminalStateTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("terminal-creator")
        self.assignee = _make_identity("terminal-assignee")
        self.service, self.sv, self.svc = _make_service_and_svc(self.creator)

    def test_no_command_leaves_completed(self):
        result = _create_activity(self.svc, self.creator.credential, self.sv)
        aid = result.activity_id
        self.svc.assign_service_activity(AssignServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-A", idempotency_key="IDEM-TA",
            occurred_at=LATER, activity_id=aid,
            assignee_identity_id=self.assignee.identity_id,
            assignment_reference="REF", assignment_basis_reference="REF",
        ))
        self.svc.accept_service_assignment(AcceptServiceAssignmentCommand(
            credential=self.assignee.credential,
            request_reference="REQ", idempotency_key="IDEM-TACC",
            occurred_at=LATER, activity_id=aid,
        ))
        self.svc.submit_service_work(SubmitServiceWorkCommand(
            credential=self.assignee.credential,
            request_reference="REQ", idempotency_key="IDEM-TSUB",
            occurred_at=LATER, activity_id=aid,
            submission_reference="REF", submission_support_references=(),
        ))
        reviewer = _make_identity("term-rev")
        self.svc.review_service_work(ReviewServiceWorkCommand(
            credential=reviewer.credential,
            request_reference="REQ", idempotency_key="IDEM-TREV",
            occurred_at=LATER, activity_id=aid,
            review_reference="REF", review_record_reference="REC",
        ))
        self.svc.complete_service_activity(CompleteServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ", idempotency_key="IDEM-TCMP",
            occurred_at=LATER, activity_id=aid,
            completion_record_reference="REF",
        ))
        with self.assertRaises(ServiceActivityLifecycleError):
            self.svc.cancel_service_activity(CancelServiceActivityCommand(
                credential=self.creator.credential,
                request_reference="REQ", idempotency_key="IDEM-CANTERM",
                occurred_at=LATER, activity_id=aid,
                cancellation_basis_reference="REF",
            ))

    def test_no_command_leaves_declined(self):
        result = _create_activity(self.svc, self.creator.credential, self.sv)
        aid = result.activity_id
        self.svc.assign_service_activity(AssignServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ", idempotency_key="IDEM-DA",
            occurred_at=LATER, activity_id=aid,
            assignee_identity_id=self.assignee.identity_id,
            assignment_reference="REF", assignment_basis_reference="REF",
        ))
        self.svc.decline_service_assignment(DeclineServiceAssignmentCommand(
            credential=self.assignee.credential,
            request_reference="REQ", idempotency_key="IDEM-DDEC",
            occurred_at=LATER, activity_id=aid,
            decline_basis_reference="REF",
        ))
        with self.assertRaises(ServiceActivityLifecycleError):
            self.svc.cancel_service_activity(CancelServiceActivityCommand(
                credential=self.creator.credential,
                request_reference="REQ", idempotency_key="IDEM-CANDEC",
                occurred_at=LATER, activity_id=aid,
                cancellation_basis_reference="REF",
            ))

    def test_no_command_leaves_cancelled(self):
        result = _create_activity(self.svc, self.creator.credential, self.sv)
        aid = result.activity_id
        self.svc.cancel_service_activity(CancelServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ", idempotency_key="IDEM-CAN1",
            occurred_at=LATER, activity_id=aid,
            cancellation_basis_reference="REF",
        ))
        with self.assertRaises(ServiceActivityLifecycleError):
            self.svc.cancel_service_activity(CancelServiceActivityCommand(
                credential=self.creator.credential,
                request_reference="REQ", idempotency_key="IDEM-CAN2",
                occurred_at=LATER, activity_id=aid,
                cancellation_basis_reference="REF",
            ))


class LifecycleEdgeTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("edge-creator")
        self.assignee = _make_identity("edge-assignee")
        self.service, self.sv, self.svc = _make_service_and_svc(self.creator)

    def test_assign_requires_unassigned(self):
        result = _create_activity(self.svc, self.creator.credential, self.sv)
        aid = result.activity_id
        self.svc.assign_service_activity(AssignServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ", idempotency_key="IDEM-EDGE-A",
            occurred_at=LATER, activity_id=aid,
            assignee_identity_id=self.assignee.identity_id,
            assignment_reference="REF", assignment_basis_reference="REF",
        ))
        # Already assigned, can't assign again
        other_assignee = _make_identity("other-assignee")
        with self.assertRaises((ServiceActivityLifecycleError, ServiceActivityConflict)):
            self.svc.assign_service_activity(AssignServiceActivityCommand(
                credential=self.creator.credential,
                request_reference="REQ", idempotency_key="IDEM-EDGE-A2",
                occurred_at=LATER, activity_id=aid,
                assignee_identity_id=other_assignee.identity_id,
                assignment_reference="REF2", assignment_basis_reference="REF2",
            ))

    def test_submit_requires_in_progress(self):
        result = _create_activity(self.svc, self.creator.credential, self.sv)
        aid = result.activity_id
        # UNASSIGNED -> can't submit
        with self.assertRaises((ServiceActivityLifecycleError, ServiceActivityCommandError)):
            self.svc.submit_service_work(SubmitServiceWorkCommand(
                credential=self.creator.credential,
                request_reference="REQ", idempotency_key="IDEM-BADSUB",
                occurred_at=LATER, activity_id=aid,
                submission_reference="REF", submission_support_references=(),
            ))


class ReplayTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("replay-creator")
        self.service, self.sv, self.svc = _make_service_and_svc(self.creator)

    def test_exact_replay_returns_original_result(self):
        idem_key = "IDEM-REPLAY"
        aid = uuid4()
        cmd = CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-REPLAY",
            idempotency_key=idem_key,
            occurred_at=NOW,
            activity_id=aid,
            service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.SERVICE,
            initiating_domain_reference="REF-INIT",
            activity_basis_reference="REF-BASIS",
        )
        result1 = self.svc.create_service_activity(cmd)
        result2 = self.svc.create_service_activity(cmd)
        self.assertEqual(result1.activity_id, result2.activity_id)
        self.assertEqual(result1.lineage_reference, result2.lineage_reference)
        self.assertEqual(result1.transition_id, result2.transition_id)
        self.assertEqual(result1.evidence, result2.evidence)

    def test_replay_does_not_change_updated_at(self):
        idem_key = "IDEM-REPLAY-TIME"
        aid = uuid4()
        cmd = CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-RPT",
            idempotency_key=idem_key,
            occurred_at=NOW,
            activity_id=aid,
            service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.SERVICE,
            initiating_domain_reference="REF-INIT",
            activity_basis_reference="REF-BASIS",
        )
        self.svc.create_service_activity(cmd)
        activity = ServiceActivity.objects.get(activity_id=aid)
        original_updated = activity.updated_at
        self.svc.create_service_activity(cmd)
        activity.refresh_from_db()
        self.assertEqual(activity.updated_at, original_updated)

    def test_replay_after_service_retirement_returns_original(self):
        aid = uuid4()
        idem_key = "IDEM-RETIRE-REPLAY"
        cmd = CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-RET",
            idempotency_key=idem_key,
            occurred_at=NOW,
            activity_id=aid,
            service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.EDUCATION,
            initiating_domain_reference="REF-EDU",
            activity_basis_reference="REF-BASIS",
        )
        result1 = self.svc.create_service_activity(cmd)
        # Retire the service
        self.service.state = Service.State.RETIRED
        self.service.save()
        result2 = self.svc.create_service_activity(cmd)
        self.assertEqual(result1.activity_id, result2.activity_id)
        self.assertEqual(result1.lineage_reference, result2.lineage_reference)

    def test_replay_after_version_succession_returns_original(self):
        aid = uuid4()
        idem_key = "IDEM-SUCC-REPLAY"
        cmd = CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-SUCC",
            idempotency_key=idem_key,
            occurred_at=NOW,
            activity_id=aid,
            service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.SERVICE,
            initiating_domain_reference="REF-INIT",
            activity_basis_reference="REF-BASIS",
        )
        result1 = self.svc.create_service_activity(cmd)
        # Successor version
        sv2 = ServiceVersion(
            service=self.service, version_number=2,
            capability_purpose="V2", domain_intent="V2",
            created_by=self.creator, created_at=NOW,
            predecessor=self.sv,
        )
        sv2.save()
        self.service.current_version = sv2
        self.service.save()
        result2 = self.svc.create_service_activity(cmd)
        self.assertEqual(result1.activity_id, result2.activity_id)
        self.assertEqual(result1.lineage_reference, result2.lineage_reference)


class ConflictTests(TestCase):
    def setUp(self):
        self.creator = _make_identity("conflict-creator")
        self.service, self.sv, self.svc = _make_service_and_svc(self.creator)

    def test_cross_epoch_conflict(self):
        aid = uuid4()
        idem_key = "IDEM-EPOCH"
        cmd = CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-EP",
            idempotency_key=idem_key,
            occurred_at=NOW,
            activity_id=aid,
            service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.SERVICE,
            initiating_domain_reference="REF",
            activity_basis_reference="REF-B",
        )
        self.svc.create_service_activity(cmd)
        # Change epoch
        self.creator.access_epoch = 1
        self.creator.save()
        with self.assertRaises(ServiceActivityCrossEpochConflict):
            self.svc.create_service_activity(cmd)

    def test_payload_conflict(self):
        aid = uuid4()
        idem_key = "IDEM-PAYLOAD"
        cmd1 = CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-P1",
            idempotency_key=idem_key,
            occurred_at=NOW,
            activity_id=aid,
            service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.SERVICE,
            initiating_domain_reference="REF",
            activity_basis_reference="REF-B1",
        )
        self.svc.create_service_activity(cmd1)
        # Different payload, same key
        cmd2 = CreateServiceActivityCommand(
            credential=self.creator.credential,
            request_reference="REQ-P1",
            idempotency_key=idem_key,
            occurred_at=NOW,
            activity_id=aid,
            service_version_pk=self.sv.pk,
            initiating_domain=InitiatingDomain.SERVICE,
            initiating_domain_reference="REF",
            activity_basis_reference="REF-B-DIFFERENT",
        )
        with self.assertRaises(ServiceActivityPayloadConflict):
            self.svc.create_service_activity(cmd2)


class RollbackResidueTests(TestCase):
    def test_failed_command_leaves_no_residue(self):
        creator = _make_identity("rollback-creator")
        service, sv, svc = _make_service_and_svc(creator)
        aid = uuid4()
        # Retire the service to cause failure after lock
        service.state = Service.State.RETIRED
        service.save()

        count_before = ServiceActivity.objects.count()
        trans_before = ServiceActivityTransition.objects.count()
        ev_before = ServiceActivityEvidenceReference.objects.count()

        with self.assertRaises(ServiceActivityLifecycleError):
            svc.create_service_activity(CreateServiceActivityCommand(
                credential=creator.credential,
                request_reference="REQ", idempotency_key="IDEM-ROLLBACK",
                occurred_at=NOW, activity_id=aid,
                service_version_pk=sv.pk,
                initiating_domain=InitiatingDomain.SERVICE,
                initiating_domain_reference="REF",
                activity_basis_reference="REF-B",
            ))

        self.assertEqual(ServiceActivity.objects.count(), count_before)
        self.assertEqual(ServiceActivityTransition.objects.count(), trans_before)
        self.assertEqual(ServiceActivityEvidenceReference.objects.count(), ev_before)


class ProviderRefusalInServiceTests(TestCase):
    def test_provider_refusal_blocks_command(self):
        creator = _make_identity("provider-refusal")
        service, sv = _make_service(creator)
        authority = ServiceCommandAuthority(provider=_RefusingProvider())
        svc = ServiceActivityService(authority=authority, clock=lambda: NOW)
        with self.assertRaises(ServiceCommandNotAuthorised):
            svc.create_service_activity(CreateServiceActivityCommand(
                credential=creator.credential,
                request_reference="REQ", idempotency_key="IDEM-REFUSE",
                occurred_at=NOW, activity_id=uuid4(),
                service_version_pk=sv.pk,
                initiating_domain=InitiatingDomain.SERVICE,
                initiating_domain_reference="REF",
                activity_basis_reference="REF-B",
            ))


class HistoricalSubjectInactivityTests(TestCase):
    """HD-1: historical non-acting subjects need not remain active."""

    def test_deactivated_creator_does_not_block_later_commands(self):
        creator = _make_identity("hist-creator")
        assignee = _make_identity("hist-assignee")
        service, sv, svc = _make_service_and_svc(creator)
        result = _create_activity(svc, creator.credential, sv)
        aid = result.activity_id
        svc.assign_service_activity(AssignServiceActivityCommand(
            credential=creator.credential,
            request_reference="REQ-A", idempotency_key="IDEM-HA",
            occurred_at=LATER, activity_id=aid,
            assignee_identity_id=assignee.identity_id,
            assignment_reference="REF-A", assignment_basis_reference="REF-AB",
        ))
        # Deactivate the creator
        creator.access_state = Identity.AccessState.DEACTIVATED
        creator.credential.is_active = False
        creator.credential.save()
        creator.save()
        # Assignee can still accept
        result = svc.accept_service_assignment(AcceptServiceAssignmentCommand(
            credential=assignee.credential,
            request_reference="REQ-ACC", idempotency_key="IDEM-HISTACCEPT",
            occurred_at=LATER, activity_id=aid,
        ))
        self.assertEqual(result.resulting_state, ServiceActivityState.IN_PROGRESS)


class OccurrenceTimeBindingTests(TestCase):
    """Every child and evidence occurrence timestamp equals the transition occurred_at."""

    def test_all_timestamps_bound_to_occurred_at(self):
        creator = _make_identity("occ-creator")
        assignee = _make_identity("occ-assignee")
        service, sv, svc = _make_service_and_svc(creator)
        cmd_time = datetime(2026, 7, 26, 15, 30, 0, tzinfo=timezone.utc)
        result = _create_activity(svc, creator.credential, sv, occurred_at=cmd_time)
        aid = result.activity_id
        activity = ServiceActivity.objects.get(activity_id=aid)
        self.assertEqual(activity.created_at, cmd_time)
        trans = ServiceActivityTransition.objects.get(pk=result.transition_id)
        self.assertEqual(trans.occurred_at, cmd_time)
        for ev in ServiceActivityEvidenceReference.objects.filter(transition=trans):
            self.assertEqual(ev.occurred_at, cmd_time)

        assign_time = datetime(2026, 7, 26, 16, 0, 0, tzinfo=timezone.utc)
        assign_result = svc.assign_service_activity(AssignServiceActivityCommand(
            credential=creator.credential,
            request_reference="REQ-OCC-A", idempotency_key="IDEM-OCC-A",
            occurred_at=assign_time, activity_id=aid,
            assignee_identity_id=assignee.identity_id,
            assignment_reference="REF-A", assignment_basis_reference="REF-AB",
        ))
        assignment = ServiceActivityAssignment.objects.get(pk=assign_result.assignment_id)
        self.assertEqual(assignment.assigned_at, assign_time)
        assign_trans = ServiceActivityTransition.objects.get(pk=assign_result.transition_id)
        self.assertEqual(assign_trans.occurred_at, assign_time)
