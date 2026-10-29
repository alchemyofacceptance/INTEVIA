from datetime import datetime, timezone
from unittest import TestCase

from src.intevia.services.library_exact_version_contract import DeterminationPayload, envelope_for


NOW = "2026-07-23T18:30:00.000000Z"


class NonDisclosureTests(TestCase):
    def payload(self, result, limitation, *, kind="DISCLOSURE"):
        return DeterminationPayload(
            action=None,
            actor_access_epoch=None,
            actor_identity_id=None,
            authority_binding_reference=None,
            basis_code="UNRESOLVED",
            binding_kind=None,
            binding_reference=None,
            binding_version=None,
            canonicalization="RFC8785+INTEVIA-S011A-v1",
            consumer_reference=None,
            determination_kind=kind,
            environment="internal-pre-alpha",
            evaluated_at=NOW,
            policy_reference="policy:LIB-EXACT-VERSION-PREALPHA-001:v1",
            provider_snapshot_reference=None,
            request_reference=None,
            requested_at=None,
            resource_id="lib.resource~hidden",
            resource_version_pk=None,
            result=result,
            revalidation_boundary="READ_TIME_ONLY",
            schema_id="intevia.s011a.library-determination",
            schema_version=1,
            source_state=None,
            unresolved_limitation_code=limitation,
            version_number=None,
            viewer_access_epoch=None,
            viewer_identity_id=None,
        )

    def test_non_positive_receipts_have_no_presentation_or_content_fields(self):
        allowed_fields = set(DeterminationPayload.__dataclass_fields__)
        prohibited = {
            "content", "title", "username", "display_name", "credential_id",
            "is_staff", "is_superuser", "event_id", "purpose", "public_error",
            "current_version", "predecessor", "successor",
        }
        self.assertFalse(allowed_fields & prohibited)
        for result, limitation in (
            ("HIDDEN", None),
            ("HOLD", "RESOURCE_OR_VERSION_UNAVAILABLE"),
        ):
            envelope = envelope_for(self.payload(result, limitation))
            self.assertNotIn(b"content", envelope.canonical_payload)
            self.assertFalse(result == "CONTENT_VISIBLE")

    def test_receipts_are_not_bearer_tokens(self):
        envelope = envelope_for(self.payload("HIDDEN", None))
        self.assertFalse(hasattr(envelope, "authorise"))
        self.assertFalse(hasattr(envelope, "permission"))

    def test_hidden_refused_hold_and_not_found_share_non_disclosing_schema(self):
        receipts = (
            envelope_for(self.payload("HIDDEN", None)),
            envelope_for(self.payload("REFUSED", None, kind="AUTHORITY")),
            envelope_for(self.payload("HOLD", "BINDING_UNAVAILABLE")),
            envelope_for(self.payload("HOLD", "RESOURCE_OR_VERSION_UNAVAILABLE")),
        )
        prohibited = (b"content", b"title", b"public_error", b"current_version", b"successor")
        expected_fields = set(DeterminationPayload.__dataclass_fields__)
        for receipt in receipts:
            self.assertEqual(set(receipt.payload.__dataclass_fields__), expected_fields)
            for field in prohibited:
                self.assertNotIn(field, receipt.canonical_payload)
