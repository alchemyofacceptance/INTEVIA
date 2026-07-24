from dataclasses import replace
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
    LibraryRequestContext,
    POLICY_ENVIRONMENT,
    POLICY_REFERENCE,
)
from src.intevia.services.library_exact_version_policy import (
    ImmutableLibraryBindingProvider,
    LibraryExactVersionPolicy,
    VIEWER_SCOPE,
)


NOW = datetime(2026, 7, 23, 18, 30, tzinfo=timezone.utc)
SNAPSHOT_REFERENCE = "lib-binding-snapshot:sha256:" + "b" * 64


class LibraryExactVersionPolicyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="s011a-policy")
        self.identity = Identity.objects.create(
            credential=self.user,
            access_state=Identity.AccessState.ACTIVE,
            access_epoch=4,
        )
        self.resource = LibraryResource.objects.create(
            resource_id="lib.resource~policy",
            created_by=self.identity,
            state=LibraryResource.State.PUBLISHED,
            created_at=NOW,
        )
        self.version = LibraryResourceVersion.objects.create(
            resource=self.resource,
            version_number=1,
            content="policy content",
            created_by=self.identity,
            created_at=NOW,
        )

    def snapshot(self, *, kind=BindingKind.ACTION, action=LibraryAction.CREATE, decision=BindingDecision.ALLOW):
        viewer = kind is BindingKind.VIEWER
        return BindingSnapshot(
            binding_reference=f"lib-authority-binding:policy.{kind.value.lower()}:v1",
            binding_version="3",
            policy_reference=POLICY_REFERENCE,
            environment=POLICY_ENVIRONMENT,
            binding_kind=kind,
            subject_identity_id=str(self.identity.identity_id),
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference=SNAPSHOT_REFERENCE,
            decision=decision,
            action=None if viewer else action,
            resource_id=None if viewer else self.resource.resource_id,
            version_number=None if viewer else "1",
            viewer_scope=VIEWER_SCOPE if viewer else None,
        )

    def policy(self, bindings=(), *, enabled=True, complete=True, available=True):
        return LibraryExactVersionPolicy(provider=ImmutableLibraryBindingProvider(
            bindings,
            enabled=enabled,
            complete_for_policy=complete,
            available=available,
        ))

    def context(self, reference="lib-authority-binding:policy.action:v1"):
        return LibraryRequestContext(
            request_reference="request.policy",
            consumer_reference="consumer.s011b",
            authority_binding_reference=reference,
            policy_reference=POLICY_REFERENCE,
            requested_at=NOW,
        )

    def authority(self, policy, action=LibraryAction.CREATE, context=None):
        return policy.determine_authority(
            identity=self.identity,
            action=action,
            resource=self.resource,
            version=self.version,
            context=context or self.context(),
            evaluated_at=NOW,
        )

    def test_all_actions_require_their_exact_binding(self):
        for action in LibraryAction:
            with self.subTest(action=action):
                snapshot = self.snapshot(action=action)
                result, _, _, limitation = self.authority(self.policy((snapshot,)), action)
                self.assertEqual(result, AuthorityResult.QUALIFIED)
                self.assertIsNone(limitation)

    def test_deny_no_match_unavailable_and_target_mismatch_are_distinct(self):
        denied = self.snapshot(decision=BindingDecision.DENY)
        self.assertEqual(self.authority(self.policy((denied,)))[0], AuthorityResult.REFUSED)
        self.assertEqual(
            self.authority(self.policy(), context=self.context("lib-authority-binding:missing:v1"))[0],
            AuthorityResult.REFUSED,
        )
        self.assertEqual(self.authority(self.policy(available=False))[0], AuthorityResult.HOLD)
        wrong_target = replace(self.snapshot(), resource_id="lib.resource~other")
        self.assertEqual(self.authority(self.policy((wrong_target,)))[0], AuthorityResult.REFUSED)

    def test_exact_deny_remains_refused(self):
        denied = self.snapshot(decision=BindingDecision.DENY)
        result, _, _, limitation = self.authority(self.policy((denied,)))
        self.assertEqual(result, AuthorityResult.REFUSED)
        self.assertIsNone(limitation)

    def test_malformed_authority_decision_holds(self):
        malformed = replace(self.snapshot(), decision="MALFORMED")
        result, _, binding, limitation = self.authority(self.policy((malformed,)))
        self.assertEqual(result, AuthorityResult.HOLD)
        self.assertIsNone(binding)
        self.assertIsNotNone(limitation)

    def test_malformed_disclosure_decision_holds(self):
        malformed = replace(self.snapshot(kind=BindingKind.VIEWER), decision=None)
        result, _, binding, limitation = self.policy((malformed,)).determine_disclosure(
            identity=self.identity,
            resource=self.resource,
            version=self.version,
            evaluated_at=NOW,
        )
        self.assertEqual(result, DisclosureResult.HOLD)
        self.assertIsNone(binding)
        self.assertIsNotNone(limitation)

    def test_wrong_binding_policy_identity_holds(self):
        malformed = replace(self.snapshot(), policy_reference="policy:OTHER:v1")
        self.assertEqual(self.authority(self.policy((malformed,)))[0], AuthorityResult.HOLD)

    def test_wrong_binding_policy_version_holds(self):
        malformed = replace(
            self.snapshot(),
            policy_reference="policy:LIB-EXACT-VERSION-PREALPHA-001:v2",
        )
        self.assertEqual(self.authority(self.policy((malformed,)))[0], AuthorityResult.HOLD)

    def test_non_active_identity_and_staff_flags_are_refused(self):
        snapshot = self.snapshot()
        for state in (
            Identity.AccessState.PENDING,
            Identity.AccessState.RESTRICTED,
            Identity.AccessState.DEACTIVATED,
        ):
            with self.subTest(state=state):
                self.identity.access_state = state
                self.assertEqual(self.authority(self.policy((snapshot,)))[0], AuthorityResult.REFUSED)
        self.identity.access_state = Identity.AccessState.ACTIVE
        self.user.is_staff = True
        self.user.save(update_fields=("is_staff",))
        self.identity.refresh_from_db()
        self.assertEqual(self.authority(self.policy((snapshot,)))[0], AuthorityResult.REFUSED)

    def test_superuser_only_credential_grants_nothing(self):
        snapshot = self.snapshot()
        viewer = self.snapshot(kind=BindingKind.VIEWER)
        self.user.is_superuser = True
        self.user.save(update_fields=("is_superuser",))
        self.identity.refresh_from_db()
        self.assertEqual(self.authority(self.policy((snapshot,)))[0], AuthorityResult.REFUSED)
        self.assertEqual(
            self.policy((viewer,)).determine_disclosure(
                identity=self.identity,
                resource=self.resource,
                version=self.version,
                evaluated_at=NOW,
            )[0],
            DisclosureResult.HIDDEN,
        )

    def test_disabled_future_expired_revoked_and_superseded_binding_hold(self):
        base = self.snapshot()
        variants = (
            replace(base, enabled=False),
            replace(base, effective_at=NOW + timedelta(seconds=1), expires_at=NOW + timedelta(hours=1)),
            replace(base, effective_at=NOW - timedelta(hours=1), expires_at=NOW),
            replace(base, revoked_at=NOW - timedelta(seconds=1)),
            replace(base, superseding_binding_reference="lib-authority-binding:successor:v1"),
        )
        for snapshot in variants:
            with self.subTest(snapshot=snapshot):
                self.assertEqual(self.authority(self.policy((snapshot,)))[0], AuthorityResult.HOLD)

    def test_disclosure_state_table_and_deprecated_asymmetry(self):
        viewer = self.snapshot(kind=BindingKind.VIEWER)
        policy = self.policy((viewer,))
        expected = {
            LibraryResource.State.PUBLISHED: DisclosureResult.CONTENT_VISIBLE,
            LibraryResource.State.DRAFT: DisclosureResult.HIDDEN,
            LibraryResource.State.DEPRECATED: DisclosureResult.CONTENT_VISIBLE,
            LibraryResource.State.ARCHIVED: DisclosureResult.HIDDEN,
        }
        for state, result in expected.items():
            with self.subTest(state=state):
                self.resource.state = state
                self.assertEqual(
                    policy.determine_disclosure(
                        identity=self.identity,
                        resource=self.resource,
                        version=self.version,
                        evaluated_at=NOW,
                    )[0],
                    result,
                )
        wrong_scope = replace(viewer, viewer_scope="OTHER_LIBRARY_SCOPE")
        self.assertEqual(
            self.policy((wrong_scope,)).determine_disclosure(
                identity=self.identity,
                resource=self.resource,
                version=self.version,
                evaluated_at=NOW,
            )[0],
            DisclosureResult.HIDDEN,
        )