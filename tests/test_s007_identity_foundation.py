from pathlib import Path
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from core.identity import CANONICAL_USERNAME_VERSION, canonical_username_v1
from core.models import (
    Identity,
    IdentityTransition,
    ProfileRole,
    ProvisioningReconciliationAttempt,
    Role,
)
from src.intevia.services.identity_service import (
    CredentialService,
    IdentityLifecycleService,
    MembershipFulfilmentRequired,
    OriginatingMembershipProvisioningService,
)


class IdentityFoundationTests(TestCase):
    def provision(self, username="Carmien"):
        return CredentialService.provision(
            username=username,
            password="governed-test-password",
            display_name="Carmien Owen",
            human_classification="HUMAN DEVELOPMENT IDENTITY",
            authority_reference="authority:provision",
            evidence_reference="evidence:provision",
            correlation_id=uuid4(),
            technical_executor="test-suite",
        )

    def fulfil(self, request, evidence="evidence:membership-fulfilled"):
        return OriginatingMembershipProvisioningService.reconcile(
            request_id=request.pk,
            state=ProvisioningReconciliationAttempt.State.FULFILLED,
            authority_reference="authority:membership-fulfilment",
            evidence_reference=evidence,
            correlation_id=uuid4(),
        )

    def activate(self, identity, request):
        evidence = "evidence:membership-fulfilled"
        self.fulfil(request, evidence)
        return IdentityLifecycleService.activate(
            identity_id=identity.identity_id,
            fulfilment_evidence_reference=evidence,
            requesting_actor=identity,
            authority_reference="authority:activate",
            technical_executor="test-suite",
            correlation_id=uuid4(),
        )

    def test_canonical_username_v1_is_versioned_and_strict(self):
        self.assertEqual(CANONICAL_USERNAME_VERSION, "canonical_username_v1")
        self.assertEqual(canonical_username_v1("  ＣＡＲＭＩＥＮ  "), "carmien")
        for invalid in ("", "two words", "name\u200bhidden", "line\nbreak"):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(ValidationError):
                    canonical_username_v1(invalid)

    def test_provisioning_creates_pending_identity_intent_and_no_role(self):
        identity, request = self.provision()

        self.assertEqual(identity.access_state, Identity.AccessState.PENDING)
        self.assertFalse(identity.credential.is_active)
        self.assertEqual(identity.access_epoch, 0)
        self.assertEqual(identity.canonical_username, "carmien")
        self.assertEqual(str(identity), "Carmien Owen")
        self.assertEqual(ProfileRole.objects.count(), 0)
        self.assertEqual(request.identity, identity)
        self.assertEqual(request.reconciliation_attempts.count(), 1)
        self.assertEqual(
            request.reconciliation_attempts.get().state,
            ProvisioningReconciliationAttempt.State.REQUESTED,
        )
        self.assertEqual(identity.access_transitions.count(), 1)

    def test_display_never_falls_back_to_credential_or_uuid(self):
        credential = User.objects.create_user(username="private-login")
        identity = Identity.objects.create(credential=credential)
        self.assertEqual(str(identity), "Historical Identity")
        self.assertNotIn(credential.username, str(identity))
        self.assertNotIn(str(identity.identity_id), str(identity))

    def test_identity_uuid_is_immutable_and_credential_is_protected(self):
        identity, _ = self.provision()
        original = identity.identity_id
        identity.identity_id = uuid4()
        with self.assertRaises(ValidationError):
            identity.save()
        identity.identity_id = original
        with self.assertRaises(ProtectedError):
            identity.credential.delete()

    def test_activation_requires_exact_fulfilment_evidence(self):
        identity, request = self.provision()
        with self.assertRaises(MembershipFulfilmentRequired):
            IdentityLifecycleService.activate(
                identity_id=identity.identity_id,
                fulfilment_evidence_reference="evidence:missing",
                requesting_actor=identity,
                authority_reference="authority:activate",
                technical_executor="test-suite",
                correlation_id=uuid4(),
            )
        identity.refresh_from_db()
        self.assertEqual(identity.access_state, Identity.AccessState.PENDING)
        self.activate(identity, request)
        identity.refresh_from_db()
        identity.credential.refresh_from_db()
        self.assertEqual(identity.access_state, Identity.AccessState.ACTIVE)
        self.assertTrue(identity.credential.is_active)

    def test_restriction_deactivation_and_reactivation_increment_epoch(self):
        identity, request = self.provision()
        self.activate(identity, request)
        identity.refresh_from_db()

        IdentityLifecycleService.change_access(
            identity_id=identity.identity_id,
            action=IdentityTransition.Action.RESTRICT,
            requesting_actor=identity,
            authority_reference="authority:restrict",
            technical_executor="test-suite",
            evidence_reference="evidence:restrict",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(),
        )
        identity.refresh_from_db()
        self.assertEqual(identity.access_epoch, 1)
        self.assertEqual(identity.access_state, Identity.AccessState.RESTRICTED)

        IdentityLifecycleService.change_access(
            identity_id=identity.identity_id,
            action=IdentityTransition.Action.DEACTIVATE,
            requesting_actor=identity,
            authority_reference="authority:deactivate",
            technical_executor="test-suite",
            evidence_reference="evidence:deactivate",
            reason_category=IdentityTransition.ReasonCategory.SECURITY,
            correlation_id=uuid4(),
        )
        identity.refresh_from_db()
        identity.credential.refresh_from_db()
        self.assertEqual(identity.access_epoch, 2)
        self.assertFalse(identity.credential.is_active)

        IdentityLifecycleService.change_access(
            identity_id=identity.identity_id,
            action=IdentityTransition.Action.REACTIVATE,
            requesting_actor=identity,
            authority_reference="authority:reactivate",
            technical_executor="test-suite",
            evidence_reference="evidence:membership-fulfilled",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(),
        )
        identity.refresh_from_db()
        identity.credential.refresh_from_db()
        self.assertEqual(identity.access_epoch, 3)
        self.assertEqual(identity.access_state, Identity.AccessState.ACTIVE)
        self.assertTrue(identity.credential.is_active)

    def test_credential_replacement_reuses_identity_and_retires_old_user(self):
        identity, request = self.provision()
        self.activate(identity, request)
        old_credential = identity.credential

        CredentialService.replace(
            identity_id=identity.identity_id,
            replacement_username="Carmien-Replacement",
            replacement_password="new-governed-test-password",
            requesting_actor=identity,
            authority_reference="authority:replace",
            evidence_reference="evidence:replace",
            correlation_id=uuid4(),
            technical_executor="test-suite",
        )

        identity.refresh_from_db()
        old_credential.refresh_from_db()
        self.assertEqual(Identity.objects.count(), 1)
        self.assertEqual(identity.canonical_username, "carmien-replacement")
        self.assertEqual(identity.access_epoch, 1)
        self.assertFalse(old_credential.is_active)
        self.assertFalse(old_credential.has_usable_password())

    def test_transition_and_reconciliation_records_are_append_only(self):
        identity, request = self.provision()
        transition = identity.access_transitions.get()
        transition.evidence_reference = "evidence:changed"
        with self.assertRaises(ValidationError):
            transition.save()
        with self.assertRaises(ValidationError):
            transition.delete()

        attempt = request.reconciliation_attempts.get()
        attempt.evidence_reference = "evidence:changed"
        with self.assertRaises(ValidationError):
            attempt.save()
        with self.assertRaises(ValidationError):
            attempt.delete()

    def test_seeded_role_vocabulary_remains_unassigned(self):
        self.assertEqual(
            set(Role.objects.values_list("name", flat=True)),
            {"User", "Gee", "Coe"},
        )
        self.assertEqual(ProfileRole.objects.count(), 0)

    def test_no_production_profilerole_assignment_call_site_exists(self):
        root = Path(__file__).resolve().parents[1]
        production_roots = (root / "core", root / "src")
        offenders = []
        for production_root in production_roots:
            for path in production_root.rglob("*.py"):
                if "migrations" in path.parts:
                    continue
                if "ProfileRole.objects.create" in path.read_text(
                    encoding="utf-8"
                ):
                    offenders.append(path.relative_to(root).as_posix())
        self.assertEqual(offenders, [])

    def test_legacy_identity_profile_is_declared_and_not_canonical(self):
        root = Path(__file__).resolve().parents[1]
        legacy_path = root / "src" / "intevia" / "identity_profile.py"
        declaration = (
            "This is a non-persistent legacy structural surface and is not "
            "canonical CORE\nIdentity."
        )
        self.assertIn(declaration, legacy_path.read_text(encoding="utf-8"))

        canonical_roots = (root / "core", root / "src" / "intevia" / "services")
        offenders = []
        for production_root in canonical_roots:
            for path in production_root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "intevia.identity_profile" in text:
                    offenders.append(path.relative_to(root).as_posix())
        self.assertEqual(offenders, [])