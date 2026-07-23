from datetime import datetime, timedelta, timezone
import pickle

from django.contrib.auth.models import User
from django.db import transaction
from django.test import TransactionTestCase

from core.models import Identity, LibraryResource, LibraryResourceVersion
from src.intevia.services.library_exact_version_contract import (
    AuthorityResult,
    BindingDecision,
    BindingKind,
    BindingSnapshot,
    LibraryAction,
    LibraryExactVersionContract,
    LibraryRequestContext,
    LinkabilityResult,
    POLICY_ENVIRONMENT,
    POLICY_REFERENCE,
)
from src.intevia.services.library_exact_version_policy import ImmutableLibraryBindingProvider, LibraryExactVersionPolicy


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