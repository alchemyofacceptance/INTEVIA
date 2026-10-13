"""Shared fixtures and evidence helpers for SE entitlement tests."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib

from core.models import OrganismEntitlementPolicyText
from src.intevia.services.organism_authority import S015AuthorityDenied


TEST_POLICY_TEXT = "TEST FIXTURE — beta offer policy text, not for production"
TEST_POLICY_VERSION = 1
TEST_POLICY_SHA256 = hashlib.sha256(TEST_POLICY_TEXT.encode("utf-8")).hexdigest()


@contextmanager
def disclose_authority_refusal_code():
    """Add refusal-code notes to the original denial without replacing it."""
    try:
        yield
    except S015AuthorityDenied as exc:
        # HG_DECISION_SE_ATTEMPT_14_REPAIR_AND_MODE_20261012 requires the original exception and traceback.
        exc.add_note(
            f"refusal_code={exc.refusal_code!r}; "
            f"disclosed_refusal_code={exc.disclosed_refusal_code!r}"
        )
        raise


def ensure_test_policy_text() -> tuple[int, str]:
    record, _ = OrganismEntitlementPolicyText.objects.get_or_create(
        policy_version=TEST_POLICY_VERSION,
        defaults={
            "accepted_text": TEST_POLICY_TEXT,
            "text_sha256": TEST_POLICY_SHA256,
        },
    )
    if (
        record.accepted_text != TEST_POLICY_TEXT
        or record.text_sha256 != TEST_POLICY_SHA256
    ):
        raise RuntimeError("the labelled SE test policy fixture conflicts with its record")
    return record.policy_version, record.text_sha256


class TestFixturePaidAvailabilitySource:
    def __init__(self, transition_reference: str | None) -> None:
        self._transition_reference = transition_reference

    def paid_availability_transition_reference(
        self,
        _living_organism_uuid,
        _effective_at,
        _known_at,
    ) -> str | None:
        return self._transition_reference
