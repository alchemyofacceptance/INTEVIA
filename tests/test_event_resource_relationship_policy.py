from dataclasses import replace
from datetime import datetime, timedelta, timezone

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Identity
from src.intevia.services.event_resource_relationship_contract import (
    BindingDecision,
    EventAuthorityBindingSnapshot,
    EventAuthorityResult,
    EventRelationshipAction,
    EventRelationshipAuthorityTarget,
    ExistenceDisclosureResult,
    RelationshipDisclosureBindingSnapshot,
    RelationshipDisclosureResult,
    RelationshipPurpose,
    RelationshipState,
)
from src.intevia.services.event_resource_relationship_policy import (
    EventResourceRelationshipPolicyV1,
    ImmutableEventAuthorityBindingProvider,
    ImmutableRelationshipDisclosureBindingProvider,
)


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


class EventResourceRelationshipPolicyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="s011b-policy")
        self.identity = Identity.objects.create(
            credential=self.user,
            access_state=Identity.AccessState.ACTIVE,
            access_epoch=5,
        )

    def target(self):
        return EventRelationshipAuthorityTarget(
            actor_identity_id=str(self.identity.identity_id),
            actor_access_epoch=5,
            action=EventRelationshipAction.CREATE,
            authority_scope="create",
            event_id="event.policy",
            event_state="draft",
            relationship_id=None,
            current_assertion_id=None,
            resource_id="library.policy",
            version_number=1,
            current_purpose=None,
            proposed_purpose=RelationshipPurpose.PREPARATION,
            occurred_at=NOW,
        )

    def authority_binding(self):
        target = self.target()
        return EventAuthorityBindingSnapshot(
            binding_reference="event-binding:authority:v1",
            binding_version=1,
            subject_identity_id=target.actor_identity_id,
            action=target.action,
            authority_scope=target.authority_scope,
            event_id=target.event_id,
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference="event-snapshot:authority:v1",
            decision=BindingDecision.ALLOW,
        )

    def disclosure_binding(self):
        return RelationshipDisclosureBindingSnapshot(
            binding_reference="event-binding:disclosure:v1",
            binding_version=1,
            subject_identity_id=str(self.identity.identity_id),
            event_id="event.policy",
            relationship_id="relationship.policy",
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference="event-snapshot:disclosure:v1",
            decision=BindingDecision.ALLOW,
        )

    def policy(self, authority=(), disclosure=(), *, enabled=True):
        return EventResourceRelationshipPolicyV1(
            authority_provider=ImmutableEventAuthorityBindingProvider(
                authority,
                enabled=enabled,
                complete_for_policy=True,
            ),
            disclosure_provider=ImmutableRelationshipDisclosureBindingProvider(
                disclosure,
                enabled=enabled,
                complete_for_policy=True,
            ),
        )

    def test_providers_are_inactive_by_default(self):
        policy = EventResourceRelationshipPolicyV1(
            authority_provider=ImmutableEventAuthorityBindingProvider(),
            disclosure_provider=ImmutableRelationshipDisclosureBindingProvider(),
        )
        envelope = policy.determine_authority(
            identity=self.identity,
            target=self.target(),
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.result, EventAuthorityResult.HOLD)

    def test_exact_authority_allow_deny_and_stale_are_distinct(self):
        allowed = self.policy((self.authority_binding(),)).determine_authority(
            identity=self.identity,
            target=self.target(),
            evaluated_at=NOW,
        )
        denied = self.policy((replace(self.authority_binding(), decision=BindingDecision.DENY),)).determine_authority(
            identity=self.identity,
            target=self.target(),
            evaluated_at=NOW,
        )
        stale = self.policy((replace(self.authority_binding(), expires_at=NOW),)).determine_authority(
            identity=self.identity,
            target=self.target(),
            evaluated_at=NOW,
        )
        self.assertEqual(allowed.result, EventAuthorityResult.QUALIFIED)
        self.assertEqual(denied.result, EventAuthorityResult.REFUSED)
        self.assertEqual(stale.result, EventAuthorityResult.HOLD)

    def test_staff_or_epoch_mismatch_grants_no_bypass(self):
        self.user.is_staff = True
        self.user.save(update_fields=("is_staff",))
        self.identity.refresh_from_db()
        envelope = self.policy((self.authority_binding(),)).determine_authority(
            identity=self.identity,
            target=self.target(),
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.result, EventAuthorityResult.REFUSED)

    def test_disclosure_binds_viewer_head_state_and_purpose(self):
        envelope = self.policy(disclosure=(self.disclosure_binding(),)).determine_disclosure(
            identity=self.identity,
            event_id="event.policy",
            relationship_id="relationship.policy",
            assertion_id=11,
            state=RelationshipState.CURRENT,
            purpose=RelationshipPurpose.DURING_EVENT,
            evaluated_at=NOW,
        )
        self.assertEqual(envelope.result, RelationshipDisclosureResult.VISIBLE)
        self.assertEqual(
            envelope.existence_result,
            ExistenceDisclosureResult.EXISTENCE_VISIBLE,
        )
        self.assertIn(b'"purpose":"DURING_EVENT"', envelope.canonical_payload)