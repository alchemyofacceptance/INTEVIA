from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Identity, LibraryResource, LibraryResourceVersion
from src.intevia.services.library_exact_version_contract import (
    AuthorityResult,
    BindingDecision,
    BindingKind,
    BindingSnapshot,
    DisclosureResult,
    LibraryAction,
    LibraryExactVersionContract,
    LibraryRequestContext,
    LinkabilityResult,
    POLICY_ENVIRONMENT,
    POLICY_REFERENCE,
)
from src.intevia.services.library_exact_version_policy import (
    ImmutableLibraryBindingProvider,
    LibraryExactVersionPolicy,
    VIEWER_SCOPE,
)


NOW = datetime(2026, 7, 23, 18, 30, tzinfo=timezone.utc)
SNAPSHOT_REFERENCE = "lib-binding-snapshot:sha256:" + "a" * 64


class LibraryExactVersionContractTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="s011a-contract")
        self.identity = Identity.objects.create(
            credential=self.user,
            access_state=Identity.AccessState.ACTIVE,
            access_epoch=7,
        )
        self.resource = LibraryResource.objects.create(
            resource_id="lib.resource~contract",
            created_by=self.identity,
            state=LibraryResource.State.PUBLISHED,
            created_at=NOW,
        )
        self.version = LibraryResourceVersion(
            pk=9_007_199_254_740_993,
            resource=self.resource,
            version_number=1,
            content="governed content",
            created_by=self.identity,
            created_at=NOW,
        )
        LibraryResourceVersion.objects.bulk_create((self.version,))
        LibraryResource.objects.filter(pk=self.resource.pk).update(current_version=self.version)
        self.resource.refresh_from_db()

    def action_binding(self, action=LibraryAction.CREATE):
        return BindingSnapshot(
            binding_reference="lib-authority-binding:contract.action:v1",
            binding_version="1",
            policy_reference=POLICY_REFERENCE,
            environment=POLICY_ENVIRONMENT,
            binding_kind=BindingKind.ACTION,
            subject_identity_id=str(self.identity.identity_id),
            enabled=True,
            effective_at=NOW - timedelta(days=1),
            expires_at=NOW + timedelta(days=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference=SNAPSHOT_REFERENCE,
            decision=BindingDecision.ALLOW,
            action=action,
            resource_id=self.resource.resource_id,
            version_number="1",
            viewer_scope=None,
        )

    def viewer_binding(self):
        return BindingSnapshot(
            binding_reference="lib-authority-binding:contract.viewer:v1",
            binding_version="1",
            policy_reference=POLICY_REFERENCE,
            environment=POLICY_ENVIRONMENT,
            binding_kind=BindingKind.VIEWER,
            subject_identity_id=str(self.identity.identity_id),
            enabled=True,
            effective_at=NOW - timedelta(days=1),
            expires_at=NOW + timedelta(days=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference=SNAPSHOT_REFERENCE,
            decision=BindingDecision.ALLOW,
            action=None,
            resource_id=None,
            version_number=None,
            viewer_scope=VIEWER_SCOPE,
        )

    def service(self, bindings=()):
        provider = ImmutableLibraryBindingProvider(
            bindings,
            enabled=True,
            complete_for_policy=True,
        )
        return LibraryExactVersionContract(policy=LibraryExactVersionPolicy(provider=provider))

    def context(self):
        return LibraryRequestContext(
            request_reference="request.contract",
            consumer_reference="consumer.s011b",
            authority_binding_reference="lib-authority-binding:contract.action:v1",
            policy_reference=POLICY_REFERENCE,
            requested_at=NOW,
        )

    def test_exact_wide_version_is_resolved_without_latest_substitution(self):
        envelope = self.service().determine_linkability(
            resource_id=self.resource.resource_id,
            version_number=1,
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.payload.result, LinkabilityResult.LINKABLE)
        self.assertEqual(envelope.payload.resource_version_pk, "9007199254740993")
        self.assertNotIn("governed content", envelope.canonical_payload.decode())
        missing = self.service().determine_linkability(
            resource_id=self.resource.resource_id,
            version_number=2,
            evaluated_at=NOW,
        )
        self.assertEqual(missing.payload.result, LinkabilityResult.HOLD)
        self.assertIsNone(missing.payload.resource_version_pk)

    def test_authority_and_disclosure_are_separate_immutable_envelopes(self):
        service = self.service((self.action_binding(), self.viewer_binding()))
        authority = service.determine_action_authority(
            actor_identity_id=self.identity.identity_id,
            resource_id=self.resource.resource_id,
            version_number=1,
            action=LibraryAction.CREATE,
            context=self.context(),
            evaluated_at=NOW,
        )
        disclosure = service.determine_disclosure(
            viewer_identity_id=self.identity.identity_id,
            resource_id=self.resource.resource_id,
            version_number=1,
            evaluated_at=NOW,
        )
        self.assertEqual(authority.payload.result, AuthorityResult.QUALIFIED)
        self.assertEqual(disclosure.payload.result, DisclosureResult.CONTENT_VISIBLE)
        self.assertNotEqual(authority.determination_reference, disclosure.determination_reference)
        with self.assertRaises(FrozenInstanceError):
            authority.payload.result = AuthorityResult.REFUSED

    def test_request_context_is_exact_and_rejects_event_or_purpose_fields(self):
        with self.assertRaises(TypeError):
            LibraryRequestContext(
                request_reference="request.contract",
                consumer_reference="consumer.s011b",
                authority_binding_reference="lib-authority-binding:contract.action:v1",
                policy_reference=POLICY_REFERENCE,
                requested_at=NOW,
                event_id="event.forbidden",
            )
        with self.assertRaises(ValueError):
            LibraryRequestContext(
                request_reference="request:colon-forbidden",
                consumer_reference="consumer.s011b",
                authority_binding_reference="lib-authority-binding:contract.action:v1",
                policy_reference=POLICY_REFERENCE,
                requested_at=NOW,
            )

    def test_purpose_field_is_rejected_directly(self):
        with self.assertRaises(TypeError):
            LibraryRequestContext(
                request_reference="request.contract",
                consumer_reference="consumer.s011b",
                authority_binding_reference="lib-authority-binding:contract.action:v1",
                policy_reference=POLICY_REFERENCE,
                requested_at=NOW,
                purpose="forbidden",
            )

    def test_wrong_policy_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            LibraryRequestContext(
                request_reference="request.contract",
                consumer_reference="consumer.s011b",
                authority_binding_reference="lib-authority-binding:contract.action:v1",
                policy_reference="policy:OTHER:v1",
                requested_at=NOW,
            )

    def test_wrong_policy_version_is_rejected(self):
        with self.assertRaises(ValueError):
            LibraryRequestContext(
                request_reference="request.contract",
                consumer_reference="consumer.s011b",
                authority_binding_reference="lib-authority-binding:contract.action:v1",
                policy_reference="policy:LIB-EXACT-VERSION-PREALPHA-001:v2",
                requested_at=NOW,
            )

    def test_missing_actor_identity_holds(self):
        envelope = self.service((self.action_binding(),)).determine_action_authority(
            actor_identity_id="11111111-2222-4333-8444-555555555555",
            resource_id=self.resource.resource_id,
            version_number=1,
            action=LibraryAction.CREATE,
            context=self.context(),
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.payload.result, AuthorityResult.HOLD)
        self.assertIsNone(envelope.payload.actor_identity_id)

    def test_missing_viewer_identity_holds(self):
        envelope = self.service((self.viewer_binding(),)).determine_disclosure(
            viewer_identity_id="11111111-2222-4333-8444-555555555555",
            resource_id=self.resource.resource_id,
            version_number=1,
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.payload.result, DisclosureResult.HOLD)
        self.assertIsNone(envelope.payload.viewer_identity_id)

    def test_mismatched_actor_identity_is_refused(self):
        other_user = User.objects.create_user(username="s011a-contract-other-actor")
        other = Identity.objects.create(credential=other_user, access_state=Identity.AccessState.ACTIVE)
        envelope = self.service((self.action_binding(),)).determine_action_authority(
            actor_identity_id=other.identity_id,
            resource_id=self.resource.resource_id,
            version_number=1,
            action=LibraryAction.CREATE,
            context=self.context(),
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.payload.result, AuthorityResult.REFUSED)
        self.assertEqual(envelope.payload.actor_identity_id, str(other.identity_id))

    def test_mismatched_viewer_identity_is_hidden(self):
        other_user = User.objects.create_user(username="s011a-contract-other-viewer")
        other = Identity.objects.create(credential=other_user, access_state=Identity.AccessState.ACTIVE)
        envelope = self.service((self.viewer_binding(),)).determine_disclosure(
            viewer_identity_id=other.identity_id,
            resource_id=self.resource.resource_id,
            version_number=1,
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.payload.result, DisclosureResult.HIDDEN)
        self.assertEqual(envelope.payload.viewer_identity_id, str(other.identity_id))

    def test_viewer_substitution_changes_bound_identity_and_digest(self):
        other_user = User.objects.create_user(username="s011a-contract-substituted-viewer")
        other = Identity.objects.create(credential=other_user, access_state=Identity.AccessState.ACTIVE)
        service = self.service((self.viewer_binding(),))
        bound = service.determine_disclosure(
            viewer_identity_id=self.identity.identity_id,
            resource_id=self.resource.resource_id,
            version_number=1,
            evaluated_at=NOW,
        )
        substituted = service.determine_disclosure(
            viewer_identity_id=other.identity_id,
            resource_id=self.resource.resource_id,
            version_number=1,
            evaluated_at=NOW,
        )
        self.assertEqual(bound.payload.result, DisclosureResult.CONTENT_VISIBLE)
        self.assertEqual(substituted.payload.result, DisclosureResult.HIDDEN)
        self.assertNotEqual(bound.determination_reference, substituted.determination_reference)

    def test_wrong_owner_exact_version_is_not_substituted(self):
        other_resource = LibraryResource.objects.create(
            resource_id="lib.resource~without-version",
            created_by=self.identity,
            state=LibraryResource.State.PUBLISHED,
            created_at=NOW,
        )
        envelope = self.service().determine_linkability(
            resource_id=other_resource.resource_id,
            version_number=self.version.version_number,
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.payload.result, LinkabilityResult.HOLD)
        self.assertIsNone(envelope.payload.resource_version_pk)

    def test_draft_linkability_is_not_linkable(self):
        self.resource.state = LibraryResource.State.DRAFT
        self.resource.save(update_fields=("state",))
        envelope = self.service().determine_linkability(
            resource_id=self.resource.resource_id,
            version_number=1,
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.payload.result, LinkabilityResult.NOT_LINKABLE)

    def test_deprecated_linkability_is_not_linkable(self):
        self.resource.state = LibraryResource.State.DEPRECATED
        self.resource.save(update_fields=("state",))
        envelope = self.service().determine_linkability(
            resource_id=self.resource.resource_id,
            version_number=1,
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.payload.result, LinkabilityResult.NOT_LINKABLE)

    def test_archived_linkability_is_not_linkable(self):
        self.resource.state = LibraryResource.State.ARCHIVED
        self.resource.save(update_fields=("state",))
        envelope = self.service().determine_linkability(
            resource_id=self.resource.resource_id,
            version_number=1,
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.payload.result, LinkabilityResult.NOT_LINKABLE)
