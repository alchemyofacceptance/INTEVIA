"""Guardians for the S013 subject-private read service."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Identity
from core.models import ProfileEffectProposalLineage
from src.intevia.services.profile_effect_authority import (
    ProjectionAuthority,
    ProjectionAuthorityResponse,
    ProposalAuthority,
    ProposalAuthorityResponse,
)
from src.intevia.services.profile_effect_contract import (
    CreateServiceSubmissionProposalCommand,
    ProfileEffectProjectionDispositionCommand,
    ProjectionState,
)
from src.intevia.services.profile_effect_read_service import ProfileEffectReadService
from src.intevia.services.profile_effect_service import (
    ProfileEffectActorError,
    ProfileEffectCrossEpochConflict,
    ProfileEffectMalformedReplay,
    ProfileEffectProjectionDispositionService,
    ServiceSubmissionProfileEffectProposalService,
)
from src.intevia.services.service_activity_read_service import ServiceActivityReadService
from tests.test_profile_effect_service import _DummyVisibilityProvider, _make_identity, _make_submitted_activity, NOW


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
            authority_reference="AUTH-PROPOSAL-READ-001",
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
            authority_reference="AUTH-PROJECTION-READ-001",
        )


class ProfileEffectReadbackTests(TestCase):
    def setUp(self):
        self.subject = _make_identity("profile-effect-reader")
        self.other = _make_identity("profile-effect-other")
        self.activity = _make_submitted_activity(self.subject)
        self.s012_read = ServiceActivityReadService(
            visibility_provider=_DummyVisibilityProvider(),
            clock=lambda: NOW,
        )
        self.proposal_service = ServiceSubmissionProfileEffectProposalService(
            authority=ProposalAuthority(provider=_ProposalProvider()),
            read_service=self.s012_read,
            clock=lambda: NOW,
        )
        self.projection_service = ProfileEffectProjectionDispositionService(
            authority=ProjectionAuthority(provider=_ProjectionProvider()),
            clock=lambda: NOW,
        )
        self.read_service = ProfileEffectReadService(clock=lambda: NOW)
        self.created = self.proposal_service.create_service_submission_proposal(
            CreateServiceSubmissionProposalCommand(
                credential=self.subject.credential,
                actor_access_epoch=self.subject.access_epoch,
                activity_id=self.activity.activity_id,
                request_reference="REQ-READ-001",
                idempotency_key="IDEM-READ-001",
                occurred_at=NOW + timedelta(minutes=10),
            )
        )
        authorised = self.projection_service.authorise_profile_effect_projection(
            ProfileEffectProjectionDispositionCommand(
                credential=self.subject.credential,
                actor_access_epoch=self.subject.access_epoch,
                lineage_id=self.created.lineage_id,
                expected_proposal_transition_pk=self.created.proposal_transition_pk,
                expected_proposal_lineage_reference=self.created.proposal_lineage_reference,
                expected_disposition_pk_or_null=None,
                expected_disposition_lineage_reference_or_null=None,
                request_reference="REQ-READ-AUTH-001",
                idempotency_key="IDEM-READ-AUTH-001",
                occurred_at=NOW + timedelta(minutes=11),
            )
        )
        self.projection_service.withdraw_profile_effect_projection(
            ProfileEffectProjectionDispositionCommand(
                credential=self.subject.credential,
                actor_access_epoch=self.subject.access_epoch,
                lineage_id=self.created.lineage_id,
                expected_proposal_transition_pk=self.created.proposal_transition_pk,
                expected_proposal_lineage_reference=self.created.proposal_lineage_reference,
                expected_disposition_pk_or_null=authorised.projection_disposition_pk,
                expected_disposition_lineage_reference_or_null=authorised.projection_lineage_reference,
                request_reference="REQ-READ-WITHDRAW-001",
                idempotency_key="IDEM-READ-WITHDRAW-001",
                occurred_at=NOW + timedelta(minutes=12),
            )
        )

    def test_subject_can_read_withdrawn_state(self):
        result = self.read_service.read_subject_profile_effect_lineage(
            credential=self.subject.credential,
            viewer_access_epoch=self.subject.access_epoch,
            lineage_id=self.created.lineage_id,
        )
        self.assertEqual(result.lineage_id, self.created.lineage_id)
        self.assertTrue(result.has_current_survivor)
        self.assertEqual(result.current_projection_state, ProjectionState.WITHDRAWN)
        self.assertEqual(len(result.proposal_history), 1)
        self.assertEqual(len(result.disposition_history), 2)

    def test_non_subject_is_denied(self):
        with self.assertRaises(ProfileEffectActorError):
            self.read_service.read_subject_profile_effect_lineage(
                credential=self.other.credential,
                viewer_access_epoch=self.other.access_epoch,
                lineage_id=self.created.lineage_id,
            )

    def test_stale_epoch_is_denied(self):
        with self.assertRaises(ProfileEffectCrossEpochConflict):
            self.read_service.read_subject_profile_effect_lineage(
                credential=self.subject.credential,
                viewer_access_epoch=self.subject.access_epoch + 1,
                lineage_id=self.created.lineage_id,
            )

    def test_readback_rejects_corrupted_current_survivor_state(self):
        ProfileEffectProposalLineage.objects.filter(
            lineage_id=self.created.lineage_id
        ).update(has_current_survivor=False)

        with self.assertRaises(ProfileEffectMalformedReplay):
            self.read_service.read_subject_profile_effect_lineage(
                credential=self.subject.credential,
                viewer_access_epoch=self.subject.access_epoch,
                lineage_id=self.created.lineage_id,
            )