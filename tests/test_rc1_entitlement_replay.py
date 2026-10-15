"""RC-1 entitlement receipt-reconstruction witnesses."""

from datetime import datetime, timezone
from uuid import uuid4

from django.db import transaction
from django.test import SimpleTestCase, TransactionTestCase

from core.models import (
    AuthorityDecision,
    Identity,
    LivingOrganismEvent,
    OrganismCommandReceipt,
    OrganismEntitlement,
    OrganismEntitlementEvent,
)
from core.organism_contract import OrganismAction
from src.intevia.services.organism_authority import S015Authority
from src.intevia.services.organism_command_support import (
    ReplayViewer,
    S015CommandReceiptMalformed,
    S015CommandReplayMismatch,
)
from src.intevia.services.organism_entitlement_service import (
    EntitlementElectionResult,
    OrganismEntitlementService,
    _election_result_from_payload,
)
from tests.sb2_route_support import prepare_route


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
RECORD_FAULT_TEXT = (
    "The earlier record of this request could not be read. This attempt made no new changes. "
    "This is a record fault, not evidence that you submitted a different request; it needs to "
    "be checked before the request is retried."
)


def _valid_payload():
    return EntitlementElectionResult(
        living_organism_uuid=uuid4(),
        offering_choice="COMPLIMENTARY_BETA",
        policy_version=1,
        entitlement_uuid=uuid4(),
        event_uuid=uuid4(),
        event_sequence=1,
        lineage_reference="lineage:entitlement",
        authority_reference="authority:entitlement",
        authority_decision_reference="decision:entitlement",
    ).receipt_payload()


class EntitlementReceiptDecoderTests(SimpleTestCase):
    def test_valid_receipt_reconstructs_without_changing_shape(self):
        payload = _valid_payload()
        self.assertEqual(_election_result_from_payload(payload).receipt_payload(), payload)

    def test_none_entitlement_uuid_is_not_a_produced_receipt_shape(self):
        payload = _valid_payload()
        payload["entitlement_uuid"] = None
        with self.assertRaises(S015CommandReceiptMalformed) as caught:
            _election_result_from_payload(payload)
        self.assertEqual(str(caught.exception), RECORD_FAULT_TEXT)

    def test_malformed_receipt_shapes_are_record_faults(self):
        payload = _valid_payload()
        malformed_payloads = [
            [],
            {key: value for key, value in payload.items() if key != "event_uuid"},
            payload | {"living_organism_uuid": 42},
            payload | {"event_uuid": 123},
            payload | {"event_sequence": True},
            payload | {"offering_choice": "UNSUPPORTED"},
            payload | {"extra": "unexpected"},
        ]
        for malformed in malformed_payloads:
            with self.subTest(payload=malformed):
                with self.assertRaises(S015CommandReceiptMalformed) as caught:
                    _election_result_from_payload(malformed)
                self.assertNotIsInstance(caught.exception, S015CommandReplayMismatch)
                self.assertEqual(str(caught.exception), RECORD_FAULT_TEXT)


class EntitlementReceiptReplayBoundaryTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.route = prepare_route()
        self.actor: Identity = self.route.founder
        self.service = OrganismEntitlementService(
            authority=self.route.birthing_service._authority,
            clock=lambda: NOW,
        )

    def _counts(self):
        return (
            AuthorityDecision.objects.count(),
            LivingOrganismEvent.objects.count(),
            OrganismEntitlement.objects.count(),
            OrganismEntitlementEvent.objects.count(),
            OrganismCommandReceipt.objects.filter(
                action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
            ).count(),
        )

    def _store_receipt(self, *, payload, suffix):
        request_reference = f"request:rc1-entitlement:{suffix}"
        idempotency_key = f"idem:rc1-entitlement:{suffix}"
        fingerprint = "a" * 64
        receipt = OrganismCommandReceipt.objects.create(
            actor=self.actor,
            action=OrganismAction.EFFECT_ENTITLEMENT_ELECTION.value,
            idempotency_key=idempotency_key,
            request_reference=request_reference,
            payload_fingerprint=fingerprint,
            authority_decision_reference="decision:rc1-entitlement",
            lineage_reference=f"lineage:rc1-entitlement:{suffix}",
            result_payload=payload,
        )
        return receipt, request_reference, idempotency_key, fingerprint

    def _attempt(self, receipt, request_reference, idempotency_key, fingerprint):
        return self.service._attempt_replay(
            actor=self.actor,
            request_reference=request_reference,
            payload_fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            viewer=ReplayViewer(self.actor.pk, self.actor.identity_id, NOW),
        )

    def test_valid_receipt_replays_and_request_mismatch_stays_distinct(self):
        payload = _valid_payload()
        receipt, request_reference, idempotency_key, fingerprint = self._store_receipt(
            payload=payload,
            suffix="valid",
        )
        expected = _election_result_from_payload(payload)
        with transaction.atomic():
            self.assertEqual(
                self._attempt(receipt, request_reference, idempotency_key, fingerprint),
                expected,
            )

        with self.assertRaises(S015CommandReplayMismatch):
            with transaction.atomic():
                self._attempt(
                    receipt,
                    f"{request_reference}:changed",
                    idempotency_key,
                    fingerprint,
                )

    def test_malformed_receipts_add_no_decision_domain_row_or_receipt(self):
        valid = _valid_payload()
        malformed_payloads = [
            {key: value for key, value in valid.items() if key != "event_uuid"},
            valid | {"living_organism_uuid": 42},
            valid | {"event_uuid": 123},
            valid | {"event_sequence": True},
            valid | {"offering_choice": "UNSUPPORTED"},
            valid | {"entitlement_uuid": None},
        ]
        for index, payload in enumerate(malformed_payloads):
            with self.subTest(index=index):
                receipt, request_reference, idempotency_key, fingerprint = self._store_receipt(
                    payload=payload,
                    suffix=f"malformed-{index}",
                )
                before = self._counts()
                with self.assertRaises(S015CommandReceiptMalformed) as caught:
                    with transaction.atomic():
                        self._attempt(receipt, request_reference, idempotency_key, fingerprint)
                self.assertEqual(str(caught.exception), RECORD_FAULT_TEXT)
                receipt.refresh_from_db()
                self.assertEqual(receipt.result_payload, payload)
                self.assertEqual(self._counts(), before)
