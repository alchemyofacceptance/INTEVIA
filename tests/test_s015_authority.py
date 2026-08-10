from dataclasses import FrozenInstanceError, asdict
from datetime import datetime, timezone
from uuid import UUID

from django.db import transaction
from django.test import TestCase

from core.organism_contract import (
    GENESIS_BOOTSTRAP_REASON,
    OrganismAction,
    QUALIFICATION_PLANE_MATRIX,
    QualificationPlane,
    QualificationPlaneState,
)
from src.intevia.services.organism_authority import (
    S015Authority,
    S015AuthorityMalformed,
    S015PreFoundingAuthorityRequest,
    S015PreFoundingAuthorityResponse,
)


class _ExactProvider:
    def evaluate_organism_authority(self, request):
        return S015PreFoundingAuthorityResponse(
            **asdict(request),
            authority_reference="authority:fixture",
        )


class _EchoMismatchProvider:
    def evaluate_organism_authority(self, request):
        values = asdict(request)
        values["request_reference"] = "request:wrong"
        return S015PreFoundingAuthorityResponse(
            **values,
            authority_reference="authority:fixture",
        )


class S015AuthorityContractTests(TestCase):
    def _request(self):
        return S015PreFoundingAuthorityRequest(
            database_alias="default",
            actor_pk=1,
            actor_identity_id=UUID(int=1),
            actor_access_epoch=0,
            action=OrganismAction.REGISTER_AUTHORITY_PRINCIPAL,
            command_subtype="REGISTER_AUTHORITY_PRINCIPAL/GENESIS_BOOTSTRAP",
            target_fingerprint="0" * 64,
            request_reference="request:fixture",
            idempotency_key="fixture",
            evaluated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def test_matrix_has_26_complete_noninheriting_rows(self):
        self.assertEqual(len(QUALIFICATION_PLANE_MATRIX), 26)
        self.assertTrue(all(len(row) == 4 for row in QUALIFICATION_PLANE_MATRIX.values()))
        genesis = QUALIFICATION_PLANE_MATRIX[
            "REGISTER_AUTHORITY_PRINCIPAL/GENESIS_BOOTSTRAP"
        ]
        self.assertEqual(genesis[QualificationPlane.EXECUTION].reason, GENESIS_BOOTSTRAP_REASON)
        self.assertIs(
            genesis[QualificationPlane.PROPAGATION].state,
            QualificationPlaneState.FORBIDDEN,
        )

    def test_request_is_frozen_and_exact_echo_qualifies(self):
        request = self._request()
        with self.assertRaises(FrozenInstanceError):
            request.idempotency_key = "changed"
        with transaction.atomic():
            result = S015Authority(provider=_ExactProvider()).qualify(request)
        self.assertRegex(result.authority_decision_reference, r"^s015d1:[0-9a-f]{64}$")

    def test_echo_mismatch_is_refused(self):
        with transaction.atomic():
            with self.assertRaises(S015AuthorityMalformed):
                S015Authority(provider=_EchoMismatchProvider()).qualify(self._request())
