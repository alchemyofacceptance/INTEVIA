from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
from unittest import TestCase

from src.intevia.services.library_exact_version_contract import (
    BindingDecision,
    BindingKind,
    BindingLookupStatus,
    BindingSnapshot,
    LibraryAction,
    POLICY_ENVIRONMENT,
    POLICY_REFERENCE,
)
from src.intevia.services.library_exact_version_policy import (
    ImmutableLibraryBindingProvider,
    binding_artifact_references,
    canonical_binding_artifact_bytes,
)


NOW = datetime(2026, 7, 23, 18, 30, tzinfo=timezone.utc)


class BindingArtifactTests(TestCase):
    def payload(self):
        return {
            "schema_id": "intevia.s011a.library-binding-artifact",
            "schema_version": 1,
            "canonicalization": "RFC8785+INTEVIA-S011A-v1",
            "policy_reference": POLICY_REFERENCE,
            "environment": POLICY_ENVIRONMENT,
            "artifact_version": "1",
            "enabled": False,
            "effective_at": "2026-07-23T00:00:00.000000Z",
            "expires_at": "2026-07-24T00:00:00.000000Z",
            "revoked_at": None,
            "superseding_artifact_reference": None,
            "complete_for_policy": False,
            "bindings": [],
        }

    def snapshot(self):
        return BindingSnapshot(
            binding_reference="lib-authority-binding:artifact.action:v1",
            binding_version="1",
            policy_reference=POLICY_REFERENCE,
            environment=POLICY_ENVIRONMENT,
            binding_kind=BindingKind.ACTION,
            subject_identity_id="11111111-2222-4333-8444-555555555555",
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference="lib-binding-snapshot:sha256:" + "c" * 64,
            decision=BindingDecision.ALLOW,
            action=LibraryAction.CREATE,
            resource_id="lib.resource~artifact",
            version_number="1",
            viewer_scope=None,
        )

    def test_canonical_artifact_has_distinct_content_addressed_references(self):
        canonical = canonical_binding_artifact_bytes(self.payload())
        self.assertEqual(canonical, json.dumps(self.payload(), separators=(",", ":"), sort_keys=True).encode())
        artifact_reference, snapshot_reference = binding_artifact_references(canonical)
        self.assertNotEqual(artifact_reference.removeprefix("lib-binding-artifact:"), snapshot_reference.removeprefix("lib-binding-snapshot:"))

    def test_unknown_fields_invalid_order_and_enabled_empty_artifact_rejected(self):
        unknown = self.payload() | {"unknown": None}
        with self.assertRaises(ValueError):
            canonical_binding_artifact_bytes(unknown)
        enabled_empty = self.payload() | {"enabled": True, "complete_for_policy": True}
        with self.assertRaises(ValueError):
            canonical_binding_artifact_bytes(enabled_empty)
        bindings = [{"binding_reference": "lib-authority-binding:z:v1"}, {"binding_reference": "lib-authority-binding:a:v1"}]
        with self.assertRaises(ValueError):
            canonical_binding_artifact_bytes(self.payload() | {"bindings": bindings})

    def test_provider_is_immutable_disabled_by_default_and_distinguishes_no_match(self):
        disabled = ImmutableLibraryBindingProvider()
        lookup = disabled.lookup(
            claimed_binding_reference="lib-authority-binding:missing:v1",
            subject_identity_id="11111111-2222-4333-8444-555555555555",
            binding_kind=BindingKind.ACTION,
            action=LibraryAction.CREATE,
            resource_id="lib.resource~artifact",
            version_number="1",
            evaluated_at=NOW,
        )
        self.assertEqual(lookup.status, BindingLookupStatus.UNAVAILABLE)
        provider = ImmutableLibraryBindingProvider((self.snapshot(),), enabled=True, complete_for_policy=True)
        with self.assertRaises(FrozenInstanceError):
            provider.enabled = False
        no_match = provider.lookup(
            claimed_binding_reference="lib-authority-binding:missing:v1",
            subject_identity_id="11111111-2222-4333-8444-555555555555",
            binding_kind=BindingKind.ACTION,
            action=LibraryAction.CREATE,
            resource_id="lib.resource~artifact",
            version_number="1",
            evaluated_at=NOW,
        )
        self.assertEqual(no_match.status, BindingLookupStatus.NO_MATCH)
