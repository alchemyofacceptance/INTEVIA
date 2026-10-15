"""RC-1 receipt-reconstruction and command-support witnesses."""

from datetime import datetime, timezone
from uuid import uuid4

from django.test import SimpleTestCase

from src.intevia.services.organism_command_support import (
    S015CommandReceiptMalformed,
    S015CommandReplayMismatch,
    S015CommandSubstrateError,
)
from src.intevia.services.organism_membership_service import (
    MembershipCommandResult,
    _expected_replay_result,
)
from src.intevia.services.organism_role_service import AssignmentCommandResult


RECORD_FAULT_TEXT = (
    "The earlier record of this request could not be read. This attempt made no new changes. "
    "This is a record fault, not evidence that you submitted a different request; it needs to "
    "be checked before the request is retried."
)


class S015CommandReceiptBoundaryTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

    def _role_payload(self):
        return AssignmentCommandResult(
            assignment_uuid=uuid4(),
            resulting_state="PROPOSED",
            event_uuid=uuid4(),
            event_sequence=1,
            event_action="PROPOSE",
            lineage_reference="lineage:role",
            temporal_basis_kind=None,
            temporal_basis_reference=None,
            effective_until=None,
            effective_until_state="NOT_ESTABLISHED",
            suspension_tolls_term=None,
            suspension_tolls_term_source=None,
            current_state_effective_at=self.now,
            state_known_at=self.now,
            state_source_event_set_fingerprint="fingerprint:role",
            authority_reference="authority:role",
            authority_decision_reference="decision:role",
        ).receipt_payload()

    def _membership_payload(self):
        return MembershipCommandResult(
            membership_uuid=uuid4(),
            resulting_state="PROPOSED",
            transition_sequence=1,
            lineage_reference="lineage:membership",
            temporal_basis_kind=None,
            temporal_basis_reference=None,
            current_state_effective_at=self.now,
            state_known_at=self.now,
            state_source_event_set_fingerprint="fingerprint:membership",
            authority_reference="authority:membership",
            authority_decision_reference="decision:membership",
        ).receipt_payload()

    def _assert_malformed(self, decoder, payload):
        with self.assertRaises(S015CommandReceiptMalformed) as caught:
            decoder(payload)
        self.assertNotIsInstance(caught.exception, S015CommandReplayMismatch)
        self.assertIsInstance(caught.exception, S015CommandSubstrateError)
        self.assertEqual(str(caught.exception), RECORD_FAULT_TEXT)

    def test_valid_role_and_membership_receipts_reconstruct(self):
        role_payload = self._role_payload()
        membership_payload = self._membership_payload()
        self.assertEqual(
            AssignmentCommandResult.from_receipt_payload(role_payload).receipt_payload(),
            role_payload,
        )
        self.assertEqual(
            _expected_replay_result(membership_payload).receipt_payload(),
            membership_payload,
        )

    def test_role_receipt_shape_faults_are_malformed(self):
        payload = self._role_payload()
        decoder = AssignmentCommandResult.from_receipt_payload
        self._assert_malformed(decoder, [])

        for key in payload:
            with self.subTest(missing=key):
                malformed = payload.copy()
                del malformed[key]
                self._assert_malformed(decoder, malformed)

        malformed = payload | {"extra": "unexpected"}
        self._assert_malformed(decoder, malformed)

        for key in (
            "assignment_uuid",
            "authority_decision_reference",
            "authority_reference",
            "current_state_effective_at",
            "effective_until_state",
            "event_action",
            "event_uuid",
            "lineage_reference",
            "resulting_state",
            "state_known_at",
            "state_source_event_set_fingerprint",
        ):
            with self.subTest(wrong_type=key):
                malformed = payload.copy()
                malformed[key] = None
                self._assert_malformed(decoder, malformed)

        for value in ("2", 2.0, True, 0):
            with self.subTest(event_sequence=value):
                malformed = payload.copy()
                malformed["event_sequence"] = value
                self._assert_malformed(decoder, malformed)

        for key, value in (
            ("event_uuid", None),
            ("assignment_uuid", "not-a-canonical-uuid"),
            ("resulting_state", "UNKNOWN"),
            ("event_action", "UNKNOWN"),
            ("effective_until_state", "UNKNOWN"),
            ("temporal_basis_kind", "UNKNOWN"),
            ("temporal_basis_reference", "orphan"),
            ("current_state_effective_at", "2026-09-28T12:00:00.000000+00:00"),
            ("state_known_at", "2026-09-28T12:00:00.000000"),
            ("suspension_tolls_term", "NO"),
        ):
            with self.subTest(field=key, value=value):
                malformed = payload.copy()
                malformed[key] = value
                self._assert_malformed(decoder, malformed)

    def test_membership_receipt_shape_faults_are_malformed(self):
        payload = self._membership_payload()
        decoder = _expected_replay_result
        self._assert_malformed(decoder, [])

        for key in payload:
            with self.subTest(missing=key):
                malformed = payload.copy()
                del malformed[key]
                self._assert_malformed(decoder, malformed)

        self._assert_malformed(decoder, payload | {"extra": "unexpected"})

        for key in (
            "authority_decision_reference",
            "authority_reference",
            "current_state_effective_at",
            "lineage_reference",
            "membership_uuid",
            "resulting_state",
            "state_known_at",
            "state_source_event_set_fingerprint",
        ):
            with self.subTest(wrong_type=key):
                malformed = payload.copy()
                malformed[key] = None
                self._assert_malformed(decoder, malformed)

        for value in ("2", 2.0, True, 0):
            with self.subTest(transition_sequence=value):
                malformed = payload.copy()
                malformed["transition_sequence"] = value
                self._assert_malformed(decoder, malformed)

        for key, value in (
            ("membership_uuid", "not-a-canonical-uuid"),
            ("resulting_state", "UNKNOWN"),
            ("temporal_basis_kind", "UNKNOWN"),
            ("temporal_basis_reference", "orphan"),
            ("current_state_effective_at", "2026-09-28T12:00:00.000000+00:00"),
            ("state_known_at", "2026-09-28T12:00:00.000000"),
        ):
            with self.subTest(field=key, value=value):
                malformed = payload.copy()
                malformed[key] = value
                self._assert_malformed(decoder, malformed)
