"""Guardians for the S013 proposal and projection authority wrappers."""

from dataclasses import asdict
from datetime import datetime, timezone
from uuid import UUID

from django.db import transaction
from django.test import TestCase

from src.intevia.services.profile_effect_authority import (
    ProjectionAuthority,
    ProjectionAuthorityNotAuthorised,
    ProjectionAuthorityRequest,
    ProjectionAuthorityResponse,
    ProposalAuthority,
    ProposalAuthorityNotAuthorised,
    ProposalAuthorityRequest,
    ProposalAuthorityResponse,
)
from src.intevia.services.profile_effect_contract import ProjectionAction, ProposalAction


NOW = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
ACTOR_UUID = UUID("11111111-1111-4111-8111-111111111111")


def _proposal_request():
    return ProposalAuthorityRequest(
        database_alias="default",
        actor_pk=1,
        actor_identity_id=ACTOR_UUID,
        actor_access_epoch=7,
        action=ProposalAction.CREATE_PROPOSAL,
        target_fingerprint="a" * 64,
        request_reference="REQ-001",
        idempotency_key="IDEM-001",
        evaluated_at=NOW,
    )


def _projection_request():
    return ProjectionAuthorityRequest(
        database_alias="default",
        actor_pk=1,
        actor_identity_id=ACTOR_UUID,
        actor_access_epoch=7,
        action=ProjectionAction.AUTHORISE_PROJECTION,
        target_fingerprint="b" * 64,
        request_reference="REQ-002",
        idempotency_key="IDEM-002",
        evaluated_at=NOW,
    )


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


class _ProposalMismatchProvider:
    def authorise(self, *, request):
        response = _ProposalProvider().authorise(request=request)
        values = asdict(response)
        values["target_fingerprint"] = "c" * 64
        return ProposalAuthorityResponse(**values)


class ProfileEffectAuthorityTests(TestCase):
    def test_proposal_authority_accepts_exact_echo(self):
        authority = ProposalAuthority(provider=_ProposalProvider())
        with transaction.atomic():
            result = authority.qualify(
                request=_proposal_request(),
                target_reference="s013pt1:" + "a" * 64,
            )
        self.assertEqual(result.decision_reference[:8], "s013pa1:")
        self.assertEqual(result.target_reference, "s013pt1:" + "a" * 64)

    def test_projection_authority_accepts_exact_echo(self):
        authority = ProjectionAuthority(provider=_ProjectionProvider())
        with transaction.atomic():
            result = authority.qualify(
                request=_projection_request(),
                target_reference="s013xt1:" + "b" * 64,
            )
        self.assertEqual(result.decision_reference[:8], "s013px1:")
        self.assertEqual(result.target_reference, "s013xt1:" + "b" * 64)

    def test_proposal_projection_types_do_not_substitute(self):
        authority = ProposalAuthority(provider=_ProjectionProvider())
        with transaction.atomic():
            with self.assertRaises(ProposalAuthorityNotAuthorised):
                authority.qualify(
                    request=_proposal_request(),
                    target_reference="s013pt1:" + "a" * 64,
                )

    def test_echo_mismatch_fails_closed(self):
        authority = ProposalAuthority(provider=_ProposalMismatchProvider())
        with transaction.atomic():
            with self.assertRaises(ProposalAuthorityNotAuthorised):
                authority.qualify(
                    request=_proposal_request(),
                    target_reference="s013pt1:" + "a" * 64,
                )

    def test_alias_mismatch_fails_closed(self):
        authority = ProjectionAuthority(provider=_ProjectionProvider())
        request = _projection_request()
        request = ProjectionAuthorityRequest(
            database_alias="other",
            actor_pk=request.actor_pk,
            actor_identity_id=request.actor_identity_id,
            actor_access_epoch=request.actor_access_epoch,
            action=request.action,
            target_fingerprint=request.target_fingerprint,
            request_reference=request.request_reference,
            idempotency_key=request.idempotency_key,
            evaluated_at=request.evaluated_at,
        )
        with transaction.atomic():
            with self.assertRaises(ProjectionAuthorityNotAuthorised):
                authority.qualify(
                    request=request,
                    target_reference="s013xt1:" + "b" * 64,
                )