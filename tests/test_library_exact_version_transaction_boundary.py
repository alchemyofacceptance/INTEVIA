from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pickle
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import transaction
from django.test import TransactionTestCase

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


class TransactionBoundaryTests(TransactionTestCase):
    def setUp(self):
        user = User.objects.create_user(username="s011a-transaction")
        self.identity = Identity.objects.create(credential=user, access_state=Identity.AccessState.ACTIVE)
        self.resource = LibraryResource.objects.create(
            resource_id="lib.resource~transaction",
            created_by=self.identity,
            state=LibraryResource.State.PUBLISHED,
            created_at=NOW,
        )
        self.version = LibraryResourceVersion.objects.create(
            resource=self.resource,
            version_number=1,
            content="transaction content",
            created_by=self.identity,
            created_at=NOW,
        )
        reference = "lib-authority-binding:transaction.action:v1"
        snapshot = BindingSnapshot(
            binding_reference=reference,
            binding_version="1",
            policy_reference=POLICY_REFERENCE,
            environment=POLICY_ENVIRONMENT,
            binding_kind=BindingKind.ACTION,
            subject_identity_id=str(self.identity.identity_id),
            enabled=True,
            effective_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            revoked_at=None,
            superseding_binding_reference=None,
            provider_snapshot_reference="lib-binding-snapshot:sha256:" + "d" * 64,
            decision=BindingDecision.ALLOW,
            action=LibraryAction.CREATE,
            resource_id=self.resource.resource_id,
            version_number="1",
            viewer_scope=None,
        )
        self.action_binding = snapshot
        provider = ImmutableLibraryBindingProvider((snapshot,), enabled=True, complete_for_policy=True)
        self.service = LibraryExactVersionContract(policy=LibraryExactVersionPolicy(provider=provider))
        self.context = LibraryRequestContext(
            request_reference="request.transaction",
            consumer_reference="consumer.s011b",
            authority_binding_reference=reference,
            policy_reference=POLICY_REFERENCE,
            requested_at=NOW,
        )

    def test_scope_requires_active_outer_transaction(self):
        with self.assertRaises(RuntimeError):
            self.service.acquire_consequential_library_scope(
                resource_id=self.resource.resource_id,
                version_number=1,
            )

    def test_scope_is_single_use_non_serializable_and_same_transaction(self):
        with transaction.atomic():
            scope = self.service.acquire_consequential_library_scope(
                resource_id=self.resource.resource_id,
                version_number=1,
            )
            with self.assertRaises(TypeError):
                pickle.dumps(scope)
            evidence = self.service.evaluate_consequential_library_truth(
                scope=scope,
                actor_identity_id=self.identity.identity_id,
                action=LibraryAction.CREATE,
                context=self.context,
                evaluated_at=NOW,
            )
            self.assertEqual(evidence.authority_envelope.payload.result, AuthorityResult.QUALIFIED)
            self.assertEqual(evidence.linkability_envelope.payload.result, LinkabilityResult.LINKABLE)
            with self.assertRaises(RuntimeError):
                self.service.evaluate_consequential_library_truth(
                    scope=scope,
                    actor_identity_id=self.identity.identity_id,
                    action=LibraryAction.CREATE,
                    context=self.context,
                    evaluated_at=NOW,
                )

    def test_amend_uses_locked_identity_and_disclosure_without_linkability(self):
        self.resource.state = LibraryResource.State.DEPRECATED
        self.resource.save(update_fields=("state",))
        action_binding = replace(
            self.action_binding,
            binding_reference="lib-authority-binding:transaction.amend:v1",
            action=LibraryAction.AMEND_PURPOSE,
        )
        viewer_binding = replace(
            action_binding,
            binding_reference="lib-authority-binding:transaction.viewer:v1",
            binding_kind=BindingKind.VIEWER,
            action=None,
            resource_id=None,
            version_number=None,
            viewer_scope=VIEWER_SCOPE,
        )
        provider = ImmutableLibraryBindingProvider(
            (action_binding, viewer_binding),
            enabled=True,
            complete_for_policy=True,
        )
        service = LibraryExactVersionContract(policy=LibraryExactVersionPolicy(provider=provider))
        context = replace(
            self.context,
            authority_binding_reference=action_binding.binding_reference,
        )

        with transaction.atomic(), patch.object(
            service,
            "determine_linkability",
            wraps=service.determine_linkability,
        ) as determine_linkability, patch.object(
            service.policy,
            "determine_authority",
            wraps=service.policy.determine_authority,
        ) as determine_authority, patch.object(
            service.policy,
            "determine_disclosure",
            wraps=service.policy.determine_disclosure,
        ) as determine_disclosure:
            scope = service.acquire_consequential_library_scope(
                resource_id=self.resource.resource_id,
                version_number=1,
            )
            evidence = service.evaluate_consequential_library_truth(
                scope=scope,
                actor_identity_id=self.identity.identity_id,
                action=LibraryAction.AMEND_PURPOSE,
                context=context,
                evaluated_at=NOW,
            )

        determine_linkability.assert_not_called()
        determine_authority.assert_called_once()
        determine_disclosure.assert_called_once()
        self.assertIsNone(evidence.linkability_envelope)
        self.assertEqual(evidence.authority_envelope.payload.result, AuthorityResult.QUALIFIED)
        self.assertEqual(evidence.disclosure_envelope.payload.result, DisclosureResult.CONTENT_VISIBLE)
        self.assertEqual(
            evidence.authority_envelope.payload.actor_identity_id,
            evidence.disclosure_envelope.payload.viewer_identity_id,
        )
        self.assertEqual(
            evidence.authority_envelope.payload.actor_access_epoch,
            evidence.disclosure_envelope.payload.viewer_access_epoch,
        )

    def test_create_and_supersede_retain_linkability_behavior(self):
        for action in (LibraryAction.CREATE, LibraryAction.SUPERSEDE_VERSION):
            with self.subTest(action=action):
                action_binding = replace(self.action_binding, action=action)
                provider = ImmutableLibraryBindingProvider(
                    (action_binding,),
                    enabled=True,
                    complete_for_policy=True,
                )
                service = LibraryExactVersionContract(
                    policy=LibraryExactVersionPolicy(provider=provider)
                )
                with transaction.atomic(), patch.object(
                    service,
                    "determine_linkability",
                    wraps=service.determine_linkability,
                ) as determine_linkability:
                    scope = service.acquire_consequential_library_scope(
                        resource_id=self.resource.resource_id,
                        version_number=1,
                    )
                    evidence = service.evaluate_consequential_library_truth(
                        scope=scope,
                        actor_identity_id=self.identity.identity_id,
                        action=action,
                        context=self.context,
                        evaluated_at=NOW,
                    )

                determine_linkability.assert_called_once()
                self.assertEqual(
                    evidence.linkability_envelope.payload.result,
                    LinkabilityResult.LINKABLE,
                )
                self.assertIsNone(evidence.disclosure_envelope)

    def test_amend_missing_target_or_identity_holds_without_linkability(self):
        for resource_id, actor_identity_id in (
            ("lib.resource~missing", self.identity.identity_id),
            (self.resource.resource_id, "11111111-2222-4333-8444-555555555555"),
        ):
            with self.subTest(resource_id=resource_id, actor_identity_id=actor_identity_id):
                with transaction.atomic(), patch.object(
                    self.service,
                    "determine_linkability",
                    wraps=self.service.determine_linkability,
                ) as determine_linkability:
                    scope = self.service.acquire_consequential_library_scope(
                        resource_id=resource_id,
                        version_number=1,
                    )
                    evidence = self.service.evaluate_consequential_library_truth(
                        scope=scope,
                        actor_identity_id=actor_identity_id,
                        action=LibraryAction.AMEND_PURPOSE,
                        context=self.context,
                        evaluated_at=NOW,
                    )

                determine_linkability.assert_not_called()
                self.assertEqual(
                    evidence.authority_envelope.payload.result,
                    AuthorityResult.HOLD,
                )
                self.assertEqual(
                    evidence.disclosure_envelope.payload.result,
                    DisclosureResult.HOLD,
                )

    def test_scope_cannot_be_used_after_transaction_exit(self):
        with transaction.atomic():
            scope = self.service.acquire_consequential_library_scope(
                resource_id=self.resource.resource_id,
                version_number=1,
            )
        with self.assertRaises(RuntimeError):
            self.service.evaluate_consequential_library_truth(
                scope=scope,
                actor_identity_id=self.identity.identity_id,
                action=LibraryAction.CREATE,
                context=self.context,
                evaluated_at=NOW,
            )

    def test_detached_receipt_is_not_accepted_as_scope(self):
        receipt = self.service.determine_linkability(
            resource_id=self.resource.resource_id,
            version_number=1,
            evaluated_at=NOW,
        )
        with transaction.atomic(), self.assertRaises(RuntimeError):
            self.service.evaluate_consequential_library_truth(
                scope=receipt,
                actor_identity_id=self.identity.identity_id,
                action=LibraryAction.CREATE,
                context=self.context,
                evaluated_at=NOW,
            )

    def test_scope_cannot_be_replayed_by_service_for_another_connection_alias(self):
        with transaction.atomic():
            scope = self.service.acquire_consequential_library_scope(
                resource_id=self.resource.resource_id,
                version_number=1,
            )
            other_service = LibraryExactVersionContract(
                policy=self.service.policy,
                database_alias="s011a_other",
            )
            with self.assertRaises(RuntimeError):
                other_service.evaluate_consequential_library_truth(
                    scope=scope,
                    actor_identity_id=self.identity.identity_id,
                    action=LibraryAction.CREATE,
                    context=self.context,
                    evaluated_at=NOW,
                )