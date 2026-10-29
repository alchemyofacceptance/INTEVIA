from pathlib import Path
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse

from core.identity import CANONICAL_USERNAME_VERSION, canonical_username_v1
from core.models import (
    Identity,
    IdentityTransition,
    LearnerRegistration,
    ProfileRole,
    ProvisioningReconciliationAttempt,
    Role,
)
from src.intevia.services.identity_service import (
    CredentialService,
    CredentialServiceRefused,
    IdentityLifecycleService,
    MembershipFulfilmentRequired,
    OriginatingMembershipProvisioningService,
)
from tests.s015_0021_support import (
    create_test_identity,
    register_test_learner,
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
        identity = create_test_identity(credential=credential)
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
        identity = register_test_learner(
            username=f"epoch-reactivation-{uuid4().hex}",
            password="governed-test-password",
            display_name="Epoch Reactivation",
        )
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
            proof_password="governed-test-password",
        )
        identity.refresh_from_db()
        identity.credential.refresh_from_db()
        self.assertEqual(identity.access_epoch, 3)
        self.assertEqual(identity.access_state, Identity.AccessState.ACTIVE)
        self.assertTrue(identity.credential.is_active)

    @skipUnless(
        connection.vendor == "postgresql",
        "P1 registration preservation requires PostgreSQL",
    )
    def test_credential_replacement_reuses_identity_and_retires_old_user(self):
        """W-PC1/W-PC2: changing a password retains the account and sign-in name."""
        old_password = "governed-test-password"
        identity = register_test_learner(
            username=f"credential-replacement-{uuid4().hex}",
            password=old_password,
            display_name="Carmien Owen",
        )
        old_credential = identity.credential
        username = old_credential.username
        new_password = "new-governed-test-password"
        registration = LearnerRegistration.objects.get(identity=identity)
        registration_before = LearnerRegistration.objects.filter(
            pk=registration.pk
        ).values().get()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT canonical_username, credential_link "
                "FROM public.core_identityresolution WHERE identity_id = %s",
                [identity.pk],
            )
            resolution_before = cursor.fetchone()
        identity_before = (
            identity.pk,
            identity.identity_id,
            identity.credential_id,
            identity.canonical_username,
            identity.access_epoch,
        )
        transition_count_before = identity.access_transitions.count()
        user_count_before = User.objects.count()

        self.assertEqual(
            authenticate(username=username, password=old_password).pk,
            old_credential.pk,
        )
        transition = CredentialService.change_password(
            identity_id=identity.identity_id,
            new_password=new_password,
            requesting_actor=identity,
            session_access_epoch=identity.access_epoch,
            correlation_id=uuid4(),
        )

        identity.refresh_from_db()
        old_credential.refresh_from_db()
        self.assertEqual(User.objects.count(), user_count_before)
        self.assertEqual(Identity.objects.count(), 1)
        self.assertEqual(
            (
                identity.pk,
                identity.identity_id,
                identity.credential_id,
                identity.canonical_username,
                identity.access_epoch,
            ),
            (
                identity_before[0],
                identity_before[1],
                identity_before[2],
                identity_before[3],
                identity_before[4] + 1,
            ),
        )
        self.assertEqual(old_credential.username, username)
        self.assertTrue(old_credential.is_active)
        self.assertTrue(old_credential.check_password(new_password))
        self.assertIsNone(authenticate(username=username, password=old_password))
        self.assertEqual(
            authenticate(username=username, password=new_password).pk,
            old_credential.pk,
        )
        old_client = Client()
        old_login = old_client.post(
            reverse("login"),
            {"username": username, "password": old_password},
        )
        self.assertNotIn("_auth_user_id", old_client.session)
        self.assertEqual(old_login.status_code, 200)
        self.assertEqual(transition.requesting_actor_id, identity.pk)
        self.assertEqual(
            transition.action,
            IdentityTransition.Action.REPLACE_CREDENTIAL,
        )
        self.assertEqual(
            transition.reason_category,
            IdentityTransition.ReasonCategory.CREDENTIAL_CHANGE,
        )
        self.assertEqual(
            transition.evidence_reference,
            f"password-change:{transition.correlation_id}",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT canonical_username, credential_link "
                "FROM public.core_identityresolution WHERE identity_id = %s",
                [identity.pk],
            )
            self.assertEqual(cursor.fetchone(), resolution_before)
        self.assertEqual(
            LearnerRegistration.objects.filter(pk=registration.pk)
            .values()
            .get(),
            registration_before,
        )
        new_client = Client()
        response = new_client.post(
            reverse("login"),
            {"username": username, "password": new_password},
        )
        self.assertTrue(response.url.startswith(reverse("shell")))

    @skipUnless(
        connection.vendor == "postgresql",
        "P1 registration preservation requires PostgreSQL",
    )
    def test_credential_account_swap_is_refused_without_changes(self):
        """W-PC7: replacing the sign-in account is refused without writes."""
        identity = register_test_learner(
            username=f"credential-account-swap-{uuid4().hex}",
            password="governed-test-password",
            display_name="Carmien Owen",
        )
        credential = identity.credential
        registration = LearnerRegistration.objects.get(identity=identity)
        registration_before = LearnerRegistration.objects.filter(
            pk=registration.pk
        ).values().get()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT canonical_username, credential_link "
                "FROM public.core_identityresolution WHERE identity_id = %s",
                [identity.pk],
            )
            resolution_before = cursor.fetchone()
        credential_password = credential.password
        identity_before = (
            identity.pk,
            identity.identity_id,
            identity.credential_id,
            identity.canonical_username,
            identity.access_epoch,
        )
        user_count_before = User.objects.count()
        transition_count_before = identity.access_transitions.count()

        with self.assertRaises(CredentialServiceRefused) as refused:
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

        self.assertEqual(
            refused.exception.code,
            "CREDENTIAL_ACCOUNT_SWAP_UNAVAILABLE",
        )
        self.assertEqual(
            str(refused.exception),
            "Your sign-in account was not replaced and nothing changed. "
            "Replacing the sign-in account itself is not available yet; you "
            "can change your password instead.",
        )
        identity.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(User.objects.count(), user_count_before)
        self.assertEqual(
            (
                identity.pk,
                identity.identity_id,
                identity.credential_id,
                identity.canonical_username,
                identity.access_epoch,
            ),
            identity_before,
        )
        self.assertEqual(identity.credential_id, credential.pk)
        self.assertEqual(identity.canonical_username, credential.username.lower())
        self.assertEqual(credential.password, credential_password)
        self.assertEqual(
            identity.access_transitions.count(),
            transition_count_before,
        )
        self.assertFalse(
            User.objects.filter(username="Carmien-Replacement").exists()
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT canonical_username, credential_link "
                "FROM public.core_identityresolution WHERE identity_id = %s",
                [identity.pk],
            )
            self.assertEqual(cursor.fetchone(), resolution_before)
        self.assertEqual(
            LearnerRegistration.objects.filter(pk=registration.pk)
            .values()
            .get(),
            registration_before,
        )

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