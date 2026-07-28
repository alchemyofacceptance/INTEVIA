"""Guardians for the S013 proposal, correction, and projection services."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import (
    Identity,
    ProfileEffectProjectionDisposition,
    ProfileEffectProposalLineage,
    ProfileEffectProposalTransition,
    Service,
    ServiceActivity,
    ServiceActivityAssignment,
    ServiceActivityEvidenceReference,
    ServiceActivityTransition,
    ServiceVersion,
    ServiceWorkSubmission,
)
from src.intevia.services.profile_effect_authority import (
    ProjectionAuthority,
    ProjectionAuthorityResponse,
    ProposalAuthority,
    ProposalAuthorityResponse,
)
from src.intevia.services.profile_effect_contract import (
    CreateServiceSubmissionProposalCommand,
    ProfileEffectProjectionDispositionCommand,
    ProfileEffectProposalCorrectionCommand,
    proposal_authority_target_payload,
    proposal_authority_target_reference,
    proposal_correction_target_payload,
    projection_authority_target_payload,
    projection_authority_target_reference,
    ProjectionAction,
    ProjectionState,
    ProposalAction,
    ProposalState,
)
from src.intevia.services.profile_effect_service import (
    ProfileEffectCrossEpochConflict,
    ProfileEffectProjectionDispositionService,
    ProfileEffectProposalCorrectionService,
    ProfileEffectMalformedReplay,
    ProfileEffectPayloadConflict,
    ServiceSubmissionProfileEffectProposalService,
)
from src.intevia.services.service_activity_read_service import ServiceActivityReadService
from src.intevia.services.service_activity_read_service import (
    ServiceCommandAction,
    _recompute_decision_reference,
    _recompute_lineage_reference,
    _recompute_payload_fingerprint,
    _recompute_target_fingerprint,
)


NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
class _DummyVisibilityProvider:
    def check_visibility(self, *, request):
        return None


class _ProposalProvider:
    def authorise(self, *, request):
        return ProposalAuthorityResponse(
            database_alias=request.database_alias,
            actor_pk=request.actor_pk,
            actor_identity_id=request.actor_identity_id,
            actor_access_epoch=request.actor_access_epoch,
            action=request.action,
            target_fingerprint=request.target_fingerprint,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            evaluated_at=request.evaluated_at,
            authority_reference="AUTH-PROPOSAL-001",
        )


class _ProjectionProvider:
    def authorise(self, *, request):
        return ProjectionAuthorityResponse(
            database_alias=request.database_alias,
            actor_pk=request.actor_pk,
            actor_identity_id=request.actor_identity_id,
            actor_access_epoch=request.actor_access_epoch,
            action=request.action,
            target_fingerprint=request.target_fingerprint,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            evaluated_at=request.evaluated_at,
            authority_reference="AUTH-PROJECTION-001",
        )


def _evidence_dicts(evidence_tuples, actor_identity_id):
    actor_text = str(actor_identity_id)
    values = [
        {
            "evidence_kind": kind,
            "reference": reference,
            "supplied_by_identity_id": actor_text,
        }
        for kind, reference in evidence_tuples
    ]
    values.sort(key=lambda item: (item["evidence_kind"].encode("utf-8"), item["reference"].encode("utf-8")))
    return values


def _make_identity(username: str) -> Identity:
    user = User.objects.create_user(username=username)
    user.is_active = True
    user.save()
    return Identity.objects.create(
        credential=user,
        access_state=Identity.AccessState.ACTIVE,
    )


def _make_submitted_activity(actor: Identity) -> ServiceActivity:
    service = Service(
        service_id=f"svc-{uuid4().hex[:8]}",
        state=Service.State.PUBLISHED,
        created_by=actor,
        created_at=NOW,
    )
    service.save()
    version = ServiceVersion(
        service=service,
        version_number=1,
        capability_purpose="Profile effect test",
        domain_intent="Profile effect test",
        created_by=actor,
        created_at=NOW,
    )
    version.save()
    service.current_version = version
    service.save()
    activity = ServiceActivity(
        activity_id=uuid4(),
        service_version=version,
        initiating_domain=ServiceActivity.InitiatingDomain.SERVICE,
        initiating_domain_reference="PE-SVC-001",
        state=ServiceActivity.State.UNASSIGNED,
        created_by=actor,
        created_at=NOW,
    )
    activity.save()
    activity_reference = activity.activity_id.hex
    timestamps = [NOW + timedelta(minutes=index) for index in range(4)]
    actions = [
        (ServiceActivityTransition.Action.CREATE, None, ServiceActivity.State.UNASSIGNED),
        (ServiceActivityTransition.Action.ASSIGN, ServiceActivity.State.UNASSIGNED, ServiceActivity.State.ASSIGNED),
        (ServiceActivityTransition.Action.ACCEPT_ASSIGNMENT, ServiceActivity.State.ASSIGNED, ServiceActivity.State.IN_PROGRESS),
        (ServiceActivityTransition.Action.SUBMIT_WORK, ServiceActivity.State.IN_PROGRESS, ServiceActivity.State.SUBMITTED),
    ]
    previous = None
    rows = []
    for index, (action, from_state, to_state) in enumerate(actions, start=1):
        if action == ServiceActivityTransition.Action.CREATE:
            target_dict = {
                "activity_id": str(activity.activity_id),
                "service_version_pk": activity.service_version_id,
            }
            command_dict = {
                "activity_basis_reference": "EVIDENCE-CREATE-001",
                "initiating_domain": activity.initiating_domain,
                "initiating_domain_reference": activity.initiating_domain_reference,
            }
            evidence_tuples = [("activity_basis", "EVIDENCE-CREATE-001")]
            target_fingerprint = _recompute_target_fingerprint(
                ServiceCommandAction.CREATE,
                activity.activity_id,
                service_version_pk=activity.service_version_id,
            )
        elif action == ServiceActivityTransition.Action.ASSIGN:
            target_dict = {
                "activity_id": str(activity.activity_id),
                "assignee_identity_id": str(actor.identity_id),
            }
            command_dict = {
                "assignee_identity_id": str(actor.identity_id),
                "assignment_basis_reference": "EVIDENCE-ASSIGN-001",
                "assignment_reference": "ASSIGN-001",
            }
            evidence_tuples = [("assignment_basis", "EVIDENCE-ASSIGN-001")]
            target_fingerprint = _recompute_target_fingerprint(
                ServiceCommandAction.ASSIGN,
                activity.activity_id,
                assignee_identity_id=str(actor.identity_id),
            )
        elif action == ServiceActivityTransition.Action.ACCEPT_ASSIGNMENT:
            target_dict = {"activity_id": str(activity.activity_id)}
            command_dict = {}
            evidence_tuples = []
            target_fingerprint = _recompute_target_fingerprint(
                ServiceCommandAction.ACCEPT_ASSIGNMENT,
                activity.activity_id,
            )
        else:
            target_dict = {"activity_id": str(activity.activity_id)}
            command_dict = {
                "submission_reference": "SUBMISSION-001",
                "submission_support_references": ["EVIDENCE-SUBMIT-001"],
            }
            evidence_tuples = [("submission_support", "EVIDENCE-SUBMIT-001")]
            target_fingerprint = _recompute_target_fingerprint(
                ServiceCommandAction.SUBMIT_WORK,
                activity.activity_id,
            )
        evidence_dicts = _evidence_dicts(evidence_tuples, actor.identity_id)
        request_reference = f"REQ-S012-{activity_reference}-{index}"
        idempotency_key = f"IDEM-S012-{activity_reference}-{index}"
        payload_fingerprint = _recompute_payload_fingerprint(
            ServiceCommandAction(action),
            actor.identity_id,
            actor.access_epoch,
            request_reference,
            idempotency_key,
            timestamps[index - 1],
            target_dict,
            command_dict,
            evidence_dicts,
        )
        lineage_reference = _recompute_lineage_reference(
            activity.activity_id,
            index,
            ServiceCommandAction(action),
            actor.identity_id,
            actor.access_epoch,
            payload_fingerprint,
            timestamps[index - 1],
        )
        decision_reference = _recompute_decision_reference(
            "default",
            actor.pk,
            actor.identity_id,
            actor.access_epoch,
            ServiceCommandAction(action),
            target_fingerprint,
            request_reference,
            idempotency_key,
            timestamps[index - 1],
            f"AUTH-S012-{index}",
        )
        row = ServiceActivityTransition(
            activity=activity,
            sequence=index,
            previous_transition=previous,
            action=action,
            from_state=from_state,
            to_state=to_state,
            actor=actor,
            actor_access_epoch=actor.access_epoch,
            authority_reference=f"AUTH-S012-{index}",
            authority_decision_reference=decision_reference,
            authority_evaluated_at=timestamps[index - 1],
            request_reference=request_reference,
            idempotency_key=idempotency_key,
            payload_fingerprint=payload_fingerprint,
            occurred_at=timestamps[index - 1],
            lineage_reference=lineage_reference,
        )
        row.save()
        rows.append(row)
        previous = row
    ServiceActivityEvidenceReference.objects.create(
        transition=rows[0],
        evidence_kind=ServiceActivityEvidenceReference.Kind.ACTIVITY_BASIS,
        reference="EVIDENCE-CREATE-001",
        supplied_by=actor,
        authority_reference="AUTH-EVIDENCE-1",
        occurred_at=rows[0].occurred_at,
    )
    ServiceActivityAssignment.objects.create(
        activity=activity,
        assignee=actor,
        assigned_by=actor,
        assignment_reference="ASSIGN-001",
        assigned_at=rows[1].occurred_at,
        transition=rows[1],
    )
    ServiceActivityEvidenceReference.objects.create(
        transition=rows[1],
        evidence_kind=ServiceActivityEvidenceReference.Kind.ASSIGNMENT_BASIS,
        reference="EVIDENCE-ASSIGN-001",
        supplied_by=actor,
        authority_reference="AUTH-EVIDENCE-2",
        occurred_at=rows[1].occurred_at,
    )
    ServiceWorkSubmission.objects.create(
        activity=activity,
        submitted_by=actor,
        submission_reference="SUBMISSION-001",
        submitted_at=rows[3].occurred_at,
        transition=rows[3],
    )
    ServiceActivityEvidenceReference.objects.create(
        transition=rows[3],
        evidence_kind=ServiceActivityEvidenceReference.Kind.SUBMISSION_SUPPORT,
        reference="EVIDENCE-SUBMIT-001",
        supplied_by=actor,
        authority_reference="AUTH-EVIDENCE-4",
        occurred_at=rows[3].occurred_at,
    )
    activity.state = ServiceActivity.State.SUBMITTED
    activity.head_transition = rows[3]
    activity.save()
    return activity


class ProfileEffectServiceTests(TestCase):
    def setUp(self):
        self.actor = _make_identity("profile-effect-service-subject")
        self.activity = _make_submitted_activity(self.actor)
        self.read_service = ServiceActivityReadService(
            visibility_provider=_DummyVisibilityProvider(),
            clock=lambda: NOW,
        )
        self.proposal_service = ServiceSubmissionProfileEffectProposalService(
            authority=ProposalAuthority(provider=_ProposalProvider()),
            read_service=self.read_service,
            clock=lambda: NOW,
        )
        self.correction_service = ProfileEffectProposalCorrectionService(
            authority=ProposalAuthority(provider=_ProposalProvider()),
            clock=lambda: NOW,
        )
        self.projection_service = ProfileEffectProjectionDispositionService(
            authority=ProjectionAuthority(provider=_ProjectionProvider()),
            clock=lambda: NOW,
        )

    def test_create_and_replay_create(self):
        command = CreateServiceSubmissionProposalCommand(
            credential=self.actor.credential,
            actor_access_epoch=self.actor.access_epoch,
            activity_id=self.activity.activity_id,
            request_reference="REQ-CREATE-001",
            idempotency_key="IDEM-CREATE-001",
            occurred_at=NOW + timedelta(minutes=10),
        )
        created = self.proposal_service.create_service_submission_proposal(command)
        replayed = self.proposal_service.create_service_submission_proposal(command)
        self.assertFalse(created.replayed)
        self.assertTrue(replayed.replayed)
        self.assertEqual(created.lineage_id, replayed.lineage_id)
        self.assertEqual(
            ProfileEffectProposalLineage.objects.count(),
            1,
        )
        self.assertEqual(ProfileEffectProposalTransition.objects.count(), 1)

    def test_create_replay_resolves_candidate_before_fresh_create_authority(self):
        command = CreateServiceSubmissionProposalCommand(
            credential=self.actor.credential,
            actor_access_epoch=self.actor.access_epoch,
            activity_id=self.activity.activity_id,
            request_reference="REQ-CREATE-ORDER-001",
            idempotency_key="IDEM-CREATE-ORDER-001",
            occurred_at=NOW + timedelta(minutes=10),
        )
        created = self.proposal_service.create_service_submission_proposal(command)
        replay_receipt = replace(created, replayed=True)

        with patch.object(
            self.proposal_service,
            "_resolve_create_replay",
            return_value=replay_receipt,
        ) as resolve_replay, patch.object(
            self.proposal_service._authority,
            "qualify",
            side_effect=AssertionError("fresh create authority ran before replay resolution"),
        ):
            replayed = self.proposal_service.create_service_submission_proposal(command)

        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.proposal_transition_pk, created.proposal_transition_pk)
        resolve_replay.assert_called_once()

    def test_create_replay_reconstructs_historical_target_from_stored_root_values(self):
        command = CreateServiceSubmissionProposalCommand(
            credential=self.actor.credential,
            actor_access_epoch=self.actor.access_epoch,
            activity_id=self.activity.activity_id,
            request_reference="REQ-CREATE-HISTORICAL-001",
            idempotency_key="IDEM-CREATE-HISTORICAL-001",
            occurred_at=NOW + timedelta(minutes=10),
        )
        created = self.proposal_service.create_service_submission_proposal(command)
        stored_lineage = ProfileEffectProposalLineage.objects.get(lineage_id=created.lineage_id)

        with patch(
            "src.intevia.services.profile_effect_service.proposal_authority_target_payload",
            wraps=proposal_authority_target_payload,
        ) as target_payload:
            replayed = self.proposal_service.create_service_submission_proposal(command)

        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.proposal_transition_pk, created.proposal_transition_pk)
        self.assertEqual(replayed.request_reference, created.request_reference)
        self.assertEqual(replayed.idempotency_key, created.idempotency_key)
        self.assertEqual(
            target_payload.call_args.kwargs["qualification_reference"],
            stored_lineage.source_qualification_reference,
        )
        self.assertEqual(
            target_payload.call_args.kwargs["subject_identity_id"],
            stored_lineage.subject.identity_id,
        )

    def test_supersede_then_void(self):
        created = self.proposal_service.create_service_submission_proposal(
            CreateServiceSubmissionProposalCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                activity_id=self.activity.activity_id,
                request_reference="REQ-CREATE-002",
                idempotency_key="IDEM-CREATE-002",
                occurred_at=NOW + timedelta(minutes=10),
            )
        )
        superseded = self.correction_service.supersede_profile_effect_proposal(
            ProfileEffectProposalCorrectionCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                lineage_id=created.lineage_id,
                expected_head_transition_pk=created.proposal_transition_pk,
                expected_head_lineage_reference=created.proposal_lineage_reference,
                request_reference="REQ-SUPERSEDE-001",
                idempotency_key="IDEM-SUPERSEDE-001",
                occurred_at=NOW + timedelta(minutes=11),
            )
        )
        voided = self.correction_service.void_profile_effect_proposal(
            ProfileEffectProposalCorrectionCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                lineage_id=created.lineage_id,
                expected_head_transition_pk=superseded.proposal_transition_pk,
                expected_head_lineage_reference=superseded.proposal_lineage_reference,
                request_reference="REQ-VOID-001",
                idempotency_key="IDEM-VOID-001",
                occurred_at=NOW + timedelta(minutes=12),
            )
        )
        self.assertEqual(superseded.action, ProposalAction.SUPERSEDE_PROPOSAL)
        self.assertEqual(superseded.to_state, ProposalState.ACTIVE)
        self.assertEqual(voided.action, ProposalAction.VOID_PROPOSAL)
        self.assertEqual(voided.to_state, ProposalState.VOIDED)
        self.assertFalse(voided.has_current_survivor)

    def test_decline_authorise_then_withdraw_projection(self):
        created = self.proposal_service.create_service_submission_proposal(
            CreateServiceSubmissionProposalCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                activity_id=self.activity.activity_id,
                request_reference="REQ-CREATE-003",
                idempotency_key="IDEM-CREATE-003",
                occurred_at=NOW + timedelta(minutes=10),
            )
        )
        declined = self.projection_service.decline_profile_effect_projection(
            ProfileEffectProjectionDispositionCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                lineage_id=created.lineage_id,
                expected_proposal_transition_pk=created.proposal_transition_pk,
                expected_proposal_lineage_reference=created.proposal_lineage_reference,
                expected_disposition_pk_or_null=None,
                expected_disposition_lineage_reference_or_null=None,
                request_reference="REQ-DECLINE-001",
                idempotency_key="IDEM-DECLINE-001",
                occurred_at=NOW + timedelta(minutes=11),
            )
        )
        authorised = self.projection_service.authorise_profile_effect_projection(
            ProfileEffectProjectionDispositionCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                lineage_id=created.lineage_id,
                expected_proposal_transition_pk=created.proposal_transition_pk,
                expected_proposal_lineage_reference=created.proposal_lineage_reference,
                expected_disposition_pk_or_null=declined.projection_disposition_pk,
                expected_disposition_lineage_reference_or_null=declined.projection_lineage_reference,
                request_reference="REQ-AUTH-001",
                idempotency_key="IDEM-AUTH-001",
                occurred_at=NOW + timedelta(minutes=12),
            )
        )
        withdrawn = self.projection_service.withdraw_profile_effect_projection(
            ProfileEffectProjectionDispositionCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                lineage_id=created.lineage_id,
                expected_proposal_transition_pk=created.proposal_transition_pk,
                expected_proposal_lineage_reference=created.proposal_lineage_reference,
                expected_disposition_pk_or_null=authorised.projection_disposition_pk,
                expected_disposition_lineage_reference_or_null=authorised.projection_lineage_reference,
                request_reference="REQ-WITHDRAW-001",
                idempotency_key="IDEM-WITHDRAW-001",
                occurred_at=NOW + timedelta(minutes=13),
            )
        )
        self.assertEqual(declined.action, ProjectionAction.DECLINE_PROJECTION)
        self.assertEqual(declined.to_state, ProjectionState.DECLINED)
        self.assertEqual(authorised.action, ProjectionAction.AUTHORISE_PROJECTION)
        self.assertEqual(authorised.to_state, ProjectionState.AUTHORISED)
        self.assertEqual(withdrawn.action, ProjectionAction.WITHDRAW_PROJECTION)
        self.assertEqual(withdrawn.to_state, ProjectionState.WITHDRAWN)

    def test_create_replay_fails_closed_on_corrupted_stored_payload_fingerprint(self):
        command = CreateServiceSubmissionProposalCommand(
            credential=self.actor.credential,
            actor_access_epoch=self.actor.access_epoch,
            activity_id=self.activity.activity_id,
            request_reference="REQ-CREATE-CORRUPT-001",
            idempotency_key="IDEM-CREATE-CORRUPT-001",
            occurred_at=NOW + timedelta(minutes=10),
        )
        created = self.proposal_service.create_service_submission_proposal(command)
        ProfileEffectProposalTransition.objects.filter(
            pk=created.proposal_transition_pk
        ).update(payload_fingerprint="f" * 64)

        with self.assertRaises(ProfileEffectPayloadConflict):
            self.proposal_service.create_service_submission_proposal(command)

    def test_supersede_replay_reconstructs_historical_predecessor_target(self):
        created = self.proposal_service.create_service_submission_proposal(
            CreateServiceSubmissionProposalCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                activity_id=self.activity.activity_id,
                request_reference="REQ-SUPERSEDE-REPLAY-CREATE-001",
                idempotency_key="IDEM-SUPERSEDE-REPLAY-CREATE-001",
                occurred_at=NOW + timedelta(minutes=10),
            )
        )
        command = ProfileEffectProposalCorrectionCommand(
            credential=self.actor.credential,
            actor_access_epoch=self.actor.access_epoch,
            lineage_id=created.lineage_id,
            expected_head_transition_pk=created.proposal_transition_pk,
            expected_head_lineage_reference=created.proposal_lineage_reference,
            request_reference="REQ-SUPERSEDE-REPLAY-001",
            idempotency_key="IDEM-SUPERSEDE-REPLAY-001",
            occurred_at=NOW + timedelta(minutes=11),
        )
        original = self.correction_service.supersede_profile_effect_proposal(command)
        replayed = self.correction_service.supersede_profile_effect_proposal(command)

        self.assertFalse(original.replayed)
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.proposal_transition_pk, original.proposal_transition_pk)
        self.assertEqual(replayed.proposal_lineage_reference, original.proposal_lineage_reference)

    def test_void_replay_reconstructs_historical_active_target_after_no_current_survivor(self):
        created = self.proposal_service.create_service_submission_proposal(
            CreateServiceSubmissionProposalCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                activity_id=self.activity.activity_id,
                request_reference="REQ-VOID-REPLAY-CREATE-001",
                idempotency_key="IDEM-VOID-REPLAY-CREATE-001",
                occurred_at=NOW + timedelta(minutes=10),
            )
        )
        command = ProfileEffectProposalCorrectionCommand(
            credential=self.actor.credential,
            actor_access_epoch=self.actor.access_epoch,
            lineage_id=created.lineage_id,
            expected_head_transition_pk=created.proposal_transition_pk,
            expected_head_lineage_reference=created.proposal_lineage_reference,
            request_reference="REQ-VOID-REPLAY-001",
            idempotency_key="IDEM-VOID-REPLAY-001",
            occurred_at=NOW + timedelta(minutes=11),
        )
        original = self.correction_service.void_profile_effect_proposal(command)
        replayed = self.correction_service.void_profile_effect_proposal(command)

        self.assertFalse(original.replayed)
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.proposal_transition_pk, original.proposal_transition_pk)
        self.assertFalse(replayed.has_current_survivor)

    def test_projection_replay_stays_bound_to_historical_transition_after_supersede(self):
        created = self.proposal_service.create_service_submission_proposal(
            CreateServiceSubmissionProposalCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                activity_id=self.activity.activity_id,
                request_reference="REQ-PROJ-HIST-CREATE-001",
                idempotency_key="IDEM-PROJ-HIST-CREATE-001",
                occurred_at=NOW + timedelta(minutes=10),
            )
        )
        projection_command = ProfileEffectProjectionDispositionCommand(
            credential=self.actor.credential,
            actor_access_epoch=self.actor.access_epoch,
            lineage_id=created.lineage_id,
            expected_proposal_transition_pk=created.proposal_transition_pk,
            expected_proposal_lineage_reference=created.proposal_lineage_reference,
            expected_disposition_pk_or_null=None,
            expected_disposition_lineage_reference_or_null=None,
            request_reference="REQ-PROJ-HIST-AUTH-001",
            idempotency_key="IDEM-PROJ-HIST-AUTH-001",
            occurred_at=NOW + timedelta(minutes=11),
        )
        original = self.projection_service.authorise_profile_effect_projection(
            projection_command
        )
        superseded = self.correction_service.supersede_profile_effect_proposal(
            ProfileEffectProposalCorrectionCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                lineage_id=created.lineage_id,
                expected_head_transition_pk=created.proposal_transition_pk,
                expected_head_lineage_reference=created.proposal_lineage_reference,
                request_reference="REQ-PROJ-HIST-SUPERSEDE-001",
                idempotency_key="IDEM-PROJ-HIST-SUPERSEDE-001",
                occurred_at=NOW + timedelta(minutes=12),
            )
        )

        replayed = self.projection_service.authorise_profile_effect_projection(
            projection_command
        )

        self.assertFalse(original.replayed)
        self.assertTrue(replayed.replayed)
        self.assertEqual(replayed.proposal_transition_pk, original.proposal_transition_pk)
        self.assertNotEqual(replayed.proposal_transition_pk, superseded.proposal_transition_pk)
        self.assertEqual(replayed.projection_disposition_pk, original.projection_disposition_pk)

    def test_projection_replay_fails_closed_on_corrupted_stored_lineage_reference(self):
        created = self.proposal_service.create_service_submission_proposal(
            CreateServiceSubmissionProposalCommand(
                credential=self.actor.credential,
                actor_access_epoch=self.actor.access_epoch,
                activity_id=self.activity.activity_id,
                request_reference="REQ-PROJ-CORRUPT-CREATE-001",
                idempotency_key="IDEM-PROJ-CORRUPT-CREATE-001",
                occurred_at=NOW + timedelta(minutes=10),
            )
        )
        command = ProfileEffectProjectionDispositionCommand(
            credential=self.actor.credential,
            actor_access_epoch=self.actor.access_epoch,
            lineage_id=created.lineage_id,
            expected_proposal_transition_pk=created.proposal_transition_pk,
            expected_proposal_lineage_reference=created.proposal_lineage_reference,
            expected_disposition_pk_or_null=None,
            expected_disposition_lineage_reference_or_null=None,
            request_reference="REQ-PROJ-CORRUPT-001",
            idempotency_key="IDEM-PROJ-CORRUPT-001",
            occurred_at=NOW + timedelta(minutes=11),
        )
        original = self.projection_service.decline_profile_effect_projection(command)
        ProfileEffectProjectionDisposition.objects.filter(
            pk=original.projection_disposition_pk
        ).update(lineage_reference="s013xl1:" + "0" * 64)

        with self.assertRaises(ProfileEffectMalformedReplay):
            self.projection_service.decline_profile_effect_projection(command)

    def _fresh_services(self, suffix):
        actor = _make_identity(f"profile-effect-replay-{suffix}")
        activity = _make_submitted_activity(actor)
        read_service = ServiceActivityReadService(
            visibility_provider=_DummyVisibilityProvider(),
            clock=lambda: NOW,
        )
        return (
            actor,
            activity,
            ServiceSubmissionProfileEffectProposalService(
                authority=ProposalAuthority(provider=_ProposalProvider()),
                read_service=read_service,
                clock=lambda: NOW,
            ),
            ProfileEffectProposalCorrectionService(
                authority=ProposalAuthority(provider=_ProposalProvider()),
                clock=lambda: NOW,
            ),
            ProfileEffectProjectionDispositionService(
                authority=ProjectionAuthority(provider=_ProjectionProvider()),
                clock=lambda: NOW,
            ),
        )

    def _assert_authority_precedes_conflict(
        self,
        *,
        service,
        invoke,
        command,
        action,
        expected_target_reference,
        occurrence_model,
        occurrence_pk,
        lineage_id,
        conflict,
    ):
        occurrence_before = occurrence_model.objects.values().get(pk=occurrence_pk)
        lineage_before = ProfileEffectProposalLineage.objects.values().get(lineage_id=lineage_id)
        proposal_count = ProfileEffectProposalTransition.objects.count()
        disposition_count = ProfileEffectProjectionDisposition.objects.count()
        authority_calls = []
        qualify = service._authority.qualify

        def observe_authority(**kwargs):
            authority_calls.append(kwargs)
            return qualify(**kwargs)

        with patch.object(service._authority, "qualify", side_effect=observe_authority):
            with self.assertRaises(conflict):
                invoke(command)

        self.assertEqual(len(authority_calls), 1)
        self.assertEqual(authority_calls[0]["request"].action, action)
        self.assertEqual(authority_calls[0]["target_reference"], expected_target_reference)
        self.assertEqual(
            authority_calls[0]["request"].target_fingerprint,
            expected_target_reference.split(":", 1)[1],
        )
        self.assertEqual(ProfileEffectProposalTransition.objects.count(), proposal_count)
        self.assertEqual(ProfileEffectProjectionDisposition.objects.count(), disposition_count)
        self.assertEqual(
            occurrence_model.objects.values().get(pk=occurrence_pk),
            occurrence_before,
        )
        self.assertEqual(
            ProfileEffectProposalLineage.objects.values().get(lineage_id=lineage_id),
            lineage_before,
        )

    def _exercise_create_authority_before_conflict(self, conflict):
        actor, activity, proposal_service, _, _ = self._fresh_services(
            f"create-{conflict.__name__.lower()}"
        )
        command = CreateServiceSubmissionProposalCommand(
            credential=actor.credential,
            actor_access_epoch=actor.access_epoch,
            activity_id=activity.activity_id,
            request_reference=f"REQ-CREATE-{conflict.__name__}",
            idempotency_key=f"IDEM-CREATE-{conflict.__name__}",
            occurred_at=NOW + timedelta(minutes=20),
        )
        receipt = proposal_service.create_service_submission_proposal(command)
        lineage = ProfileEffectProposalLineage.objects.get(lineage_id=receipt.lineage_id)
        expected_target = proposal_authority_target_reference(
            proposal_authority_target_payload(
                database_alias="default",
                qualification_reference=lineage.source_qualification_reference,
                subject_identity_id=lineage.subject.identity_id,
            )
        )
        if conflict is ProfileEffectCrossEpochConflict:
            actor.access_epoch += 1
            actor.save(update_fields=["access_epoch"])
            replay_command = replace(command, actor_access_epoch=actor.access_epoch)
        else:
            replay_command = replace(command, occurred_at=command.occurred_at + timedelta(seconds=1))
        self._assert_authority_precedes_conflict(
            service=proposal_service,
            invoke=proposal_service.create_service_submission_proposal,
            command=replay_command,
            action=ProposalAction.CREATE_PROPOSAL,
            expected_target_reference=expected_target,
            occurrence_model=ProfileEffectProposalTransition,
            occurrence_pk=receipt.proposal_transition_pk,
            lineage_id=receipt.lineage_id,
            conflict=conflict,
        )

    def _exercise_correction_authority_before_conflict(self, action, conflict):
        suffix = f"{action.value.lower()}-{conflict.__name__.lower()}"
        actor, activity, proposal_service, correction_service, _ = self._fresh_services(suffix)
        created = proposal_service.create_service_submission_proposal(
            CreateServiceSubmissionProposalCommand(
                credential=actor.credential,
                actor_access_epoch=actor.access_epoch,
                activity_id=activity.activity_id,
                request_reference=f"REQ-CREATE-{suffix}",
                idempotency_key=f"IDEM-CREATE-{suffix}",
                occurred_at=NOW + timedelta(minutes=20),
            )
        )
        command = ProfileEffectProposalCorrectionCommand(
            credential=actor.credential,
            actor_access_epoch=actor.access_epoch,
            lineage_id=created.lineage_id,
            expected_head_transition_pk=created.proposal_transition_pk,
            expected_head_lineage_reference=created.proposal_lineage_reference,
            request_reference=f"REQ-{suffix}",
            idempotency_key=f"IDEM-{suffix}",
            occurred_at=NOW + timedelta(minutes=21),
        )
        invoke = {
            ProposalAction.SUPERSEDE_PROPOSAL: correction_service.supersede_profile_effect_proposal,
            ProposalAction.VOID_PROPOSAL: correction_service.void_profile_effect_proposal,
        }[action]
        receipt = invoke(command)
        lineage = ProfileEffectProposalLineage.objects.get(lineage_id=created.lineage_id)
        predecessor = ProfileEffectProposalTransition.objects.get(pk=created.proposal_transition_pk)
        expected_target = proposal_authority_target_reference(
            proposal_correction_target_payload(
                database_alias="default",
                lineage_id=lineage.lineage_id,
                subject_identity_id=lineage.subject.identity_id,
                current_proposal_transition_pk=predecessor.pk,
                current_proposal_lineage_reference=predecessor.lineage_reference,
                current_proposal_state=ProposalState(predecessor.to_state),
                has_current_survivor=True,
                action=action,
            )
        )
        if conflict is ProfileEffectCrossEpochConflict:
            actor.access_epoch += 1
            actor.save(update_fields=["access_epoch"])
            replay_command = replace(command, actor_access_epoch=actor.access_epoch)
        else:
            replay_command = replace(command, occurred_at=command.occurred_at + timedelta(seconds=1))
        self._assert_authority_precedes_conflict(
            service=correction_service,
            invoke=invoke,
            command=replay_command,
            action=action,
            expected_target_reference=expected_target,
            occurrence_model=ProfileEffectProposalTransition,
            occurrence_pk=receipt.proposal_transition_pk,
            lineage_id=created.lineage_id,
            conflict=conflict,
        )

    def _exercise_projection_authority_before_conflict(self, action, conflict):
        suffix = f"{action.value.lower()}-{conflict.__name__.lower()}"
        actor, activity, proposal_service, _, projection_service = self._fresh_services(suffix)
        created = proposal_service.create_service_submission_proposal(
            CreateServiceSubmissionProposalCommand(
                credential=actor.credential,
                actor_access_epoch=actor.access_epoch,
                activity_id=activity.activity_id,
                request_reference=f"REQ-CREATE-{suffix}",
                idempotency_key=f"IDEM-CREATE-{suffix}",
                occurred_at=NOW + timedelta(minutes=20),
            )
        )
        previous = None
        if action is ProjectionAction.WITHDRAW_PROJECTION:
            previous = projection_service.authorise_profile_effect_projection(
                ProfileEffectProjectionDispositionCommand(
                    credential=actor.credential,
                    actor_access_epoch=actor.access_epoch,
                    lineage_id=created.lineage_id,
                    expected_proposal_transition_pk=created.proposal_transition_pk,
                    expected_proposal_lineage_reference=created.proposal_lineage_reference,
                    expected_disposition_pk_or_null=None,
                    expected_disposition_lineage_reference_or_null=None,
                    request_reference=f"REQ-AUTHORISE-{suffix}",
                    idempotency_key=f"IDEM-AUTHORISE-{suffix}",
                    occurred_at=NOW + timedelta(minutes=21),
                )
            )
        command = ProfileEffectProjectionDispositionCommand(
            credential=actor.credential,
            actor_access_epoch=actor.access_epoch,
            lineage_id=created.lineage_id,
            expected_proposal_transition_pk=created.proposal_transition_pk,
            expected_proposal_lineage_reference=created.proposal_lineage_reference,
            expected_disposition_pk_or_null=(previous.projection_disposition_pk if previous else None),
            expected_disposition_lineage_reference_or_null=(previous.projection_lineage_reference if previous else None),
            request_reference=f"REQ-{suffix}",
            idempotency_key=f"IDEM-{suffix}",
            occurred_at=NOW + timedelta(minutes=22),
        )
        invoke = {
            ProjectionAction.AUTHORISE_PROJECTION: projection_service.authorise_profile_effect_projection,
            ProjectionAction.DECLINE_PROJECTION: projection_service.decline_profile_effect_projection,
            ProjectionAction.WITHDRAW_PROJECTION: projection_service.withdraw_profile_effect_projection,
        }[action]
        receipt = invoke(command)
        lineage = ProfileEffectProposalLineage.objects.get(lineage_id=created.lineage_id)
        predecessor = (
            ProfileEffectProjectionDisposition.objects.get(pk=previous.projection_disposition_pk)
            if previous
            else None
        )
        expected_target = projection_authority_target_reference(
            projection_authority_target_payload(
                database_alias="default",
                lineage_id=lineage.lineage_id,
                subject_identity_id=lineage.subject.identity_id,
                current_proposal_transition_pk=created.proposal_transition_pk,
                current_proposal_lineage_reference=created.proposal_lineage_reference,
                current_disposition_pk_or_null=(predecessor.pk if predecessor else None),
                current_disposition_lineage_reference_or_null=(predecessor.lineage_reference if predecessor else None),
                current_projection_state=(ProjectionState(predecessor.to_state) if predecessor else ProjectionState.UNAUTHORISED),
                action=action,
            )
        )
        if conflict is ProfileEffectCrossEpochConflict:
            actor.access_epoch += 1
            actor.save(update_fields=["access_epoch"])
            replay_command = replace(command, actor_access_epoch=actor.access_epoch)
        else:
            replay_command = replace(command, occurred_at=command.occurred_at + timedelta(seconds=1))
        self._assert_authority_precedes_conflict(
            service=projection_service,
            invoke=invoke,
            command=replay_command,
            action=action,
            expected_target_reference=expected_target,
            occurrence_model=ProfileEffectProjectionDisposition,
            occurrence_pk=receipt.projection_disposition_pk,
            lineage_id=created.lineage_id,
            conflict=conflict,
        )

    def test_create_replay_qualifies_authority_before_stored_epoch_conflict(self):
        self._exercise_create_authority_before_conflict(ProfileEffectCrossEpochConflict)

    def test_create_replay_qualifies_authority_before_payload_conflict(self):
        self._exercise_create_authority_before_conflict(ProfileEffectPayloadConflict)

    def test_correction_replay_qualifies_each_authority_before_stored_epoch_conflict(self):
        for action in (ProposalAction.SUPERSEDE_PROPOSAL, ProposalAction.VOID_PROPOSAL):
            with self.subTest(action=action):
                self._exercise_correction_authority_before_conflict(
                    action,
                    ProfileEffectCrossEpochConflict,
                )

    def test_correction_replay_qualifies_each_authority_before_payload_conflict(self):
        for action in (ProposalAction.SUPERSEDE_PROPOSAL, ProposalAction.VOID_PROPOSAL):
            with self.subTest(action=action):
                self._exercise_correction_authority_before_conflict(action, ProfileEffectPayloadConflict)

    def test_projection_replay_qualifies_each_authority_before_stored_epoch_conflict(self):
        for action in ProjectionAction:
            with self.subTest(action=action):
                self._exercise_projection_authority_before_conflict(
                    action,
                    ProfileEffectCrossEpochConflict,
                )

    def test_projection_replay_qualifies_each_authority_before_payload_conflict(self):
        for action in ProjectionAction:
            with self.subTest(action=action):
                self._exercise_projection_authority_before_conflict(action, ProfileEffectPayloadConflict)