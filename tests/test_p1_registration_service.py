"""Service witnesses for the P1 same-account password-change operation."""

import datetime as dt
import hashlib
import io
import json
from uuid import uuid4
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import (
    DatabaseError,
    IntegrityError,
    connection,
    connections,
    transaction,
)
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from core.identity import canonical_username_v1
from core.session import (
    SESSION_ACCESS_EPOCH_KEY,
    SESSION_IDENTITY_KEY,
    SESSION_STARTED_AT_KEY,
)
from core.models import (
    Identity,
    IdentityTransition,
    LearnerRegistration,
    OrganismCommandReceipt,
    PlatformTermsAcceptance,
    PlatformTermsVersion,
    RegistrationReceipt,
)
from src.intevia.services.identity_service import (
    CredentialService,
    CredentialServiceRefused,
    IdentityLifecycleRefused,
    IdentityLifecycleService,
    InvalidIdentityTransition,
    LearnerRegistrationService,
    RegistrationServiceRefused,
)
from src.intevia.services.organism_application_authority import (
    ProductionGovernedRegistrationFactSource,
    fixture_terms_permitted,
)
from src.intevia.services.organism_authority import identity_state_at
from src.intevia.services.organism_membership_service import (
    OrganismMembershipReader,
)
from tests.s015_0021_support import (
    create_test_identity,
    register_with_current_terms,
    register_test_learner,
)


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "P1 password-change witnesses require PostgreSQL",
)


@POSTGRESQL_ONLY
class P1PasswordChangeTests(TransactionTestCase):
    reset_sequences = True
    old_password = "Existing-Governed-Password-1"
    first_new_password = "First-Governed-Password-2"
    second_new_password = "Second-Governed-Password-3"

    def setUp(self):
        self.identity = register_test_learner(
            username=f"p1-password-{uuid4().hex}",
            password="governed-test-password",
            display_name="P1 password witness",
        )
        self.identity.credential.set_password(self.old_password)
        self.identity.credential.save(update_fields=("password",))
        self.identity.refresh_from_db()
        self.registration = LearnerRegistration.objects.get(
            identity=self.identity
        )

    def change(self, password, *, requesting_actor, session_access_epoch):
        return CredentialService.change_password(
            identity_id=self.identity.identity_id,
            new_password=password,
            requesting_actor=requesting_actor,
            session_access_epoch=session_access_epoch,
            correlation_id=uuid4(),
        )

    def account_binding(self):
        identity = Identity.objects.select_related("credential").get(
            pk=self.identity.pk
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT canonical_username, credential_link "
                "FROM public.core_identityresolution WHERE identity_id = %s",
                [identity.pk],
            )
            resolution = cursor.fetchone()
        registration = LearnerRegistration.objects.filter(
            identity_id=identity.pk
        ).values().get()
        return (
            identity.pk,
            identity.credential_id,
            identity.credential.username,
            identity.canonical_username,
            identity.identity_id,
            resolution,
            registration,
        )

    def test_django_session_hash_protection_alone(self):
        """W-PC3(i): Django invalidates the old session while the epoch stays fixed."""
        client = Client()
        response = client.post(
            reverse("login"),
            {
                "username": self.identity.credential.username,
                "password": self.old_password,
            },
        )
        self.assertTrue(response.url.startswith(reverse("shell")))
        self.assertEqual(client.get(reverse("shell")).status_code, 200)
        starting_epoch = self.identity.access_epoch
        old_hash = self.identity.credential.password
        django_level_password = "Changed-At-Django-Level-4"
        self.identity.credential.set_password(django_level_password)
        self.identity.credential.save(update_fields=("password",))

        self.identity.refresh_from_db()
        self.identity.credential.refresh_from_db()
        self.assertEqual(self.identity.access_epoch, starting_epoch)
        self.assertNotEqual(self.identity.credential.password, old_hash)
        self.assertTrue(
            client.get(reverse("shell")).url.startswith(reverse("login"))
        )

    def test_identity_epoch_protection_alone(self):
        """W-PC3(ii): the INTEVIA epoch invalidates the old session with password fixed."""
        client = Client()
        response = client.post(
            reverse("login"),
            {
                "username": self.identity.credential.username,
                "password": self.old_password,
            },
        )
        self.assertTrue(response.url.startswith(reverse("shell")))
        password_hash = self.identity.credential.password
        self.identity.access_epoch += 1
        self.identity.save(update_fields=("access_epoch",))

        self.identity.refresh_from_db()
        self.identity.credential.refresh_from_db()
        self.assertEqual(self.identity.credential.password, password_hash)
        self.assertTrue(
            client.get(reverse("shell")).url.startswith(reverse("login"))
        )

    def test_password_change_invalidates_session_and_rejects_stale_write(self):
        """W-PC3(iii), SE-A1: the changing session and any old-epoch write are refused."""
        client = Client()
        response = client.post(
            reverse("login"),
            {
                "username": self.identity.credential.username,
                "password": self.old_password,
            },
        )
        self.assertTrue(response.url.startswith(reverse("shell")))
        starting_epoch = self.identity.access_epoch
        self.change(
            self.first_new_password,
            requesting_actor=self.identity,
            session_access_epoch=starting_epoch,
        )
        self.assertTrue(
            client.get(reverse("shell")).url.startswith(reverse("login"))
        )
        identity = Identity.objects.get(pk=self.identity.pk)
        credential = identity.credential
        transitions_before = IdentityTransition.objects.filter(
            identity=identity
        ).count()
        registration_before = LearnerRegistration.objects.filter(
            identity_id=identity.pk
        ).values().get()
        password_hash_before = credential.password

        with self.assertRaises(CredentialServiceRefused) as stale:
            self.change(
                self.second_new_password,
                requesting_actor=self.identity,
                session_access_epoch=starting_epoch,
            )

        self.assertEqual(stale.exception.code, "PASSWORD_CHANGE_SESSION_STALE")
        self.assertEqual(
            str(stale.exception),
            "Your password was not changed and nothing changed. Your session is "
            "out of date because your account's security settings changed; sign "
            "in again, then try again.",
        )
        identity.refresh_from_db()
        identity.credential.refresh_from_db()
        self.assertEqual(identity.access_epoch, starting_epoch + 1)
        self.assertEqual(identity.credential.password, password_hash_before)
        self.assertEqual(
            IdentityTransition.objects.filter(identity=identity).count(),
            transitions_before,
        )
        self.assertEqual(
            LearnerRegistration.objects.filter(identity_id=identity.pk)
            .values()
            .get(),
            registration_before,
        )

    def test_self_service_and_current_epoch_checks(self):
        """SE-A3: only the identity itself and a session bound to the current epoch pass."""
        other = create_test_identity(
            username=f"p1-other-{uuid4().hex}",
            access_state=Identity.AccessState.ACTIVE,
        )
        password_hash = self.identity.credential.password
        transition_count = IdentityTransition.objects.filter(
            identity=self.identity
        ).count()
        with self.assertRaises(CredentialServiceRefused) as unauthorized:
            self.change(
                self.first_new_password,
                requesting_actor=other,
                session_access_epoch=self.identity.access_epoch,
            )
        self.assertEqual(
            unauthorized.exception.code,
            "PASSWORD_CHANGE_UNAUTHORIZED",
        )
        self.assertEqual(
            self.identity.credential.password,
            password_hash,
        )
        self.assertEqual(
            IdentityTransition.objects.filter(identity=self.identity).count(),
            transition_count,
        )

        with self.assertRaises(CredentialServiceRefused) as stale:
            self.change(
                self.first_new_password,
                requesting_actor=self.identity,
                session_access_epoch=self.identity.access_epoch + 1,
            )
        self.assertEqual(stale.exception.code, "PASSWORD_CHANGE_SESSION_STALE")
        self.identity.refresh_from_db()
        self.identity.credential.refresh_from_db()
        self.assertEqual(self.identity.access_epoch, 0)
        self.assertEqual(self.identity.credential.password, password_hash)
        self.assertEqual(
            IdentityTransition.objects.filter(identity=self.identity).count(),
            transition_count,
        )

    def test_password_change_obeys_s007_access_state_rules(self):
        """S007 permits the service only for ACTIVE or RESTRICTED identities."""
        self.identity.access_state = Identity.AccessState.RESTRICTED
        self.identity.save(update_fields=("access_state",))
        transition = self.change(
            self.first_new_password,
            requesting_actor=self.identity,
            session_access_epoch=self.identity.access_epoch,
        )
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.access_state, Identity.AccessState.RESTRICTED)
        self.assertEqual(transition.action, IdentityTransition.Action.REPLACE_CREDENTIAL)

        self.identity.access_state = Identity.AccessState.PENDING
        self.identity.save(update_fields=("access_state",))
        password_hash = self.identity.credential.password
        transition_count = IdentityTransition.objects.filter(
            identity=self.identity
        ).count()
        with self.assertRaises(InvalidIdentityTransition):
            self.change(
                self.second_new_password,
                requesting_actor=self.identity,
                session_access_epoch=self.identity.access_epoch,
            )
        self.identity.refresh_from_db()
        self.identity.credential.refresh_from_db()
        self.assertEqual(self.identity.credential.password, password_hash)
        self.assertEqual(
            IdentityTransition.objects.filter(identity=self.identity).count(),
            transition_count,
        )

    def test_rejected_password_writes_nothing(self):
        """W-PC6: a Django validator rejection preserves account and registration state."""
        rejected_password = "x"
        with self.assertRaises(ValidationError) as validator_refusal:
            validate_password(rejected_password, user=self.identity.credential)
        binding_before = self.account_binding()
        epoch_before = self.identity.access_epoch
        password_hash_before = self.identity.credential.password
        transitions_before = IdentityTransition.objects.filter(
            identity=self.identity
        ).count()

        with self.assertRaises(CredentialServiceRefused) as refused:
            self.change(
                rejected_password,
                requesting_actor=self.identity,
                session_access_epoch=epoch_before,
            )

        self.assertEqual(refused.exception.code, "PASSWORD_REJECTED")
        self.assertEqual(
            str(refused.exception),
            "; ".join(validator_refusal.exception.messages),
        )
        self.assertEqual(self.account_binding(), binding_before)
        self.identity.refresh_from_db()
        self.identity.credential.refresh_from_db()
        self.assertEqual(self.identity.access_epoch, epoch_before)
        self.assertEqual(self.identity.credential.password, password_hash_before)
        self.assertEqual(
            IdentityTransition.objects.filter(identity=self.identity).count(),
            transitions_before,
        )

    def test_sequential_password_changes_preserve_transition_chain(self):
        """W-PC4 sequential case: successful changes advance once and link continuously."""
        binding_before = self.account_binding()
        starting_epoch = self.identity.access_epoch
        first = self.change(
            self.first_new_password,
            requesting_actor=self.identity,
            session_access_epoch=starting_epoch,
        )
        self.identity.refresh_from_db()
        binding_after_first = self.account_binding()
        self.assertEqual(binding_after_first[:5], binding_before[:5])
        self.assertEqual(binding_after_first[5:], binding_before[5:])
        second_session_epoch = self.identity.access_epoch
        second = self.change(
            self.second_new_password,
            requesting_actor=self.identity,
            session_access_epoch=second_session_epoch,
        )
        self.assertIsNotNone(first.recorded_at)
        self.assertIsNotNone(second.recorded_at)
        transitions = list(
            IdentityTransition.objects.filter(
                identity=self.identity,
                action=IdentityTransition.Action.REPLACE_CREDENTIAL,
            ).order_by("occurred_at", "pk")
        )
        self.identity.refresh_from_db()
        self.assertEqual(self.identity.access_epoch, starting_epoch + 2)
        self.assertEqual(transitions, [first, second])
        provision = IdentityTransition.objects.get(
            identity=self.identity,
            action=IdentityTransition.Action.PROVISION,
        )
        activation = IdentityTransition.objects.get(
            identity=self.identity,
            action=IdentityTransition.Action.ACTIVATE,
        )
        self.assertIsNone(provision.previous_transition_id)
        self.assertEqual(activation.previous_transition_id, provision.pk)
        self.assertEqual(first.previous_transition_id, activation.pk)
        self.assertEqual(second.previous_transition_id, first.pk)
        self.assertEqual(self.account_binding(), binding_before)
        self.assertTrue(
            authenticate(
                username=self.identity.credential.username,
                password=self.second_new_password,
            )
        )
        self.assertFalse(
            authenticate(
                username=self.identity.credential.username,
                password=self.first_new_password,
            )
        )

    def test_transition_and_receipt_data_exclude_password_material(self):
        """W-PC5: the transition, receipts, and operation log contain no password or hash."""
        identity = Identity.objects.get(pk=self.identity.pk)
        receipts_before = list(
            OrganismCommandReceipt.objects.filter(actor=identity).values()
        )
        with self.assertNoLogs(
            "src.intevia.services.identity_service",
            level="DEBUG",
        ):
            transition = self.change(
                self.first_new_password,
                requesting_actor=self.identity,
                session_access_epoch=self.identity.access_epoch,
            )
        identity.refresh_from_db()
        identity.credential.refresh_from_db()
        transition_values = [
            str(getattr(transition, field.attname))
            for field in IdentityTransition._meta.concrete_fields
        ]
        password_material = (self.old_password, self.first_new_password)
        password_hash = identity.credential.password
        self.assertFalse(
            any(
                material in value
                for material in (*password_material, password_hash)
                for value in transition_values
            )
        )
        receipts_after = list(
            OrganismCommandReceipt.objects.filter(actor=identity).values()
        )
        self.assertEqual(
            receipts_after,
            receipts_before,
        )
        receipt_values = [
            str(value) for receipt in receipts_after for value in receipt.values()
        ]
        self.assertFalse(
            any(
                material in value
                for material in (*password_material, password_hash)
                for value in receipt_values
            )
        )


@POSTGRESQL_ONLY
class P1RegistrationServiceTests(TransactionTestCase):
    reset_sequences = True
    registration_password = "Registration-Governed-Password-1"
    registration_tables = (
        "auth_user",
        "core_identity",
        "core_identityresolution",
        "core_platformtermsversion",
        "core_platformtermsacceptance",
        "core_registrationreceipt",
        "core_learnerregistration",
        "core_identitytransition",
    )
    membership_tables = (
        "core_organismmembership",
        "core_organismmembershiptransition",
        "core_contextualroleassignment",
        "core_contextualroleassignmentstateevent",
        "core_membershipcondition",
        "core_membershipconditionstateevent",
        "core_application",
        "core_applicationlineage",
        "core_applicationlineageevent",
        "core_csaassignment",
        "core_officeappointmentconsent",
        "core_circle",
        "core_originatingmembershipprovisioningrequest",
        "core_provisioningreconciliationattempt",
        "core_profilerole",
    )

    def setUp(self):
        self.terms_version = self.create_terms_version()

    def create_terms_version(
        self,
        *,
        version_label="FIXTURE-DEV-1",
        is_fixture=True,
        effective_from=None,
        supersedes=None,
    ):
        terms_body = f"Governed registration terms {uuid4()}."
        return PlatformTermsVersion.objects.create(
            terms_version_uuid=uuid4(),
            version_label=version_label,
            text_commitment=(
                "s015r1:"
                + hashlib.sha256(terms_body.encode("utf-8")).hexdigest()
            ),
            text_body=terms_body,
            cited_rulings=[],
            published_by_authority_reference="fixture:registration",
            is_fixture=is_fixture,
            effective_from=effective_from
            or dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1),
            supersedes=supersedes,
        )

    def register(self, *, username=None, terms_version=None, **overrides):
        terms_version_uuid = overrides.pop("terms_version_uuid", None)
        if terms_version_uuid is None and terms_version is not None:
            terms_version_uuid = terms_version.terms_version_uuid
        return register_with_current_terms(
            username=username or f"p1-registration-{uuid4().hex}",
            password=overrides.pop("password", self.registration_password),
            display_name=overrides.pop(
                "display_name", "P1 governed registration"
            ),
            terms_version_uuid=terms_version_uuid,
            terms_accepted=overrides.pop("terms_accepted", True),
            correlation_id=overrides.pop("correlation_id", uuid4()),
            **overrides,
        )

    @staticmethod
    def table_counts(tables):
        counts = {}
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f"SELECT count(*) FROM public.{table}")
                counts[table] = cursor.fetchone()[0]
        return counts

    @staticmethod
    def database_clock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            return cursor.fetchone()[0]

    @staticmethod
    def resolution_for(identity):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT canonical_username, credential_link "
                "FROM public.core_identityresolution WHERE identity_id = %s",
                [identity.pk],
            )
            return cursor.fetchone()

    def assert_receipt_diagnostics(self, error, version):
        self.assertEqual(
            error.submitted_version,
            version,
        )
        self.assertIsNotNone(error.effective_at)
        self.assertEqual(error.effective_at, error.received_at)
        self.assertIn("Registration receipt diagnostics:", str(error))
        if version is not None:
            self.assertIn(str(version.terms_version_uuid), str(error))
            self.assertIn(str(version.effective_from), str(error))
            self.assertIn(str(version.effective_until), str(error))
            self.assertIn(str(version.recorded_at), str(error))

    @staticmethod
    def canonical_string(value):
        return json.dumps(value, ensure_ascii=False)

    @classmethod
    def canonical_registration_value(cls, pg_type, value):
        if pg_type == "timestamptz":
            timestamp = value.astimezone(dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            return cls.canonical_string(timestamp)
        if pg_type == "uuid":
            return cls.canonical_string(str(value))
        if pg_type in {"varchar", "text"}:
            return cls.canonical_string(value)
        if pg_type == "bigint":
            return str(int(value))
        raise AssertionError(f"unsupported registration commitment type: {pg_type}")

    @classmethod
    def expected_registration_commitment(cls, registration):
        fields = (
            ("actor_access_epoch", "bigint", registration.actor_access_epoch),
            ("authority_reference", "varchar", registration.authority_reference),
            ("effective_at", "timestamptz", registration.effective_at),
            ("enterer_executor", "varchar", registration.enterer_executor),
            ("entry_mode", "varchar", registration.entry_mode),
            ("evidence_reference", "varchar", registration.evidence_reference),
            ("identity_id", "bigint", registration.identity_id),
            ("received_at", "timestamptz", registration.received_at),
            ("recorded_at", "timestamptz", registration.recorded_at),
            ("registration_receipt_id", "bigint", registration.registration_receipt_id),
            ("registration_uuid", "uuid", registration.registration_uuid),
        )
        preimage = "{" + ",".join(
            f"{cls.canonical_string(name)}:"
            f"{cls.canonical_registration_value(pg_type, value)}"
            for name, pg_type, value in fields
        ) + "}"
        return "s015r1:" + hashlib.sha256(preimage.encode("utf-8")).hexdigest()

    def test_self_registration_signs_in_after_atomic_activation(self):
        """W-A1.1 - SELF service: the complete registration activates one sign-in account."""
        username = f"p1-login-{uuid4().hex}"
        registration = self.register(username=username)
        identity = Identity.objects.select_related("credential").get(
            pk=registration.identity_id
        )
        transitions = list(
            IdentityTransition.objects.filter(identity=identity).order_by(
                "occurred_at", "pk"
            )
        )

        self.assertEqual(identity.access_state, Identity.AccessState.ACTIVE)
        self.assertTrue(identity.credential.is_active)
        self.assertEqual(
            [transition.action for transition in transitions],
            [
                IdentityTransition.Action.PROVISION,
                IdentityTransition.Action.ACTIVATE,
            ],
        )
        self.assertEqual(transitions[0].prior_state, None)
        self.assertEqual(
            transitions[0].resulting_state,
            Identity.AccessState.PENDING,
        )
        self.assertEqual(
            transitions[1].prior_state,
            Identity.AccessState.PENDING,
        )
        self.assertEqual(
            transitions[1].resulting_state,
            Identity.AccessState.ACTIVE,
        )
        for transition in transitions:
            self.assertEqual(
                transition.authority_reference,
                f"learner-registration:{registration.registration_uuid}",
            )
            self.assertEqual(
                transition.evidence_reference,
                registration.evidence_reference,
            )
            self.assertEqual(
                transition.reason_category,
                IdentityTransition.ReasonCategory.GOVERNED_ACTIVATION,
            )
            self.assertEqual(transition.requesting_actor_id, identity.pk)
            self.assertIsNotNone(transition.effective_at)
            self.assertIsNotNone(transition.received_at)
        self.assertIsNone(transitions[0].previous_transition_id)
        self.assertEqual(
            transitions[1].previous_transition_id,
            transitions[0].pk,
        )
        self.assertFalse(identity.originating_membership_requests.exists())

        client = Client()
        response = client.post(
            reverse("login"),
            {
                "username": username,
                "password": self.registration_password,
            },
        )
        self.assertTrue(response.url.startswith(reverse("shell")))
        self.assertEqual(client.get(reverse("shell")).status_code, 200)

    def test_registration_record_is_immutable_and_uses_database_time(self):
        """W-A1.3 - SELF service: database time, row immutability, and commitment recomputation."""
        before = self.database_clock()
        registration = self.register()
        after = self.database_clock()
        registration.refresh_from_db()

        self.assertIsNotNone(registration.recorded_at)
        self.assertLessEqual(before, registration.recorded_at)
        self.assertLessEqual(registration.recorded_at, after)
        self.assertEqual(
            registration.registration_commitment,
            self.expected_registration_commitment(registration),
        )
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                LearnerRegistration.objects.filter(pk=registration.pk).update(
                    authority_reference="changed"
                )
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                LearnerRegistration.objects.filter(pk=registration.pk).delete()
        self.assertTrue(
            LearnerRegistration.objects.filter(pk=registration.pk).exists()
        )

    def test_production_registration_fact_source_checks_both_coordinates(self):
        """W-A1.4 - founding/application source: effective and knowledge coordinates gate the reference."""
        registration = self.register()
        source = ProductionGovernedRegistrationFactSource()
        identity_uuid = registration.identity.identity_id
        reference = f"learner-registration:{registration.registration_uuid}"
        known_after = max(
            registration.received_at,
            registration.recorded_at,
        ) + dt.timedelta(microseconds=1)

        self.assertIsNone(
            source.reference_at(
                identity_uuid,
                registration.effective_at - dt.timedelta(microseconds=1),
                known_after,
            )
        )
        self.assertIsNone(
            source.reference_at(
                identity_uuid,
                registration.effective_at,
                registration.received_at - dt.timedelta(microseconds=1),
            )
        )
        self.assertIsNone(
            source.reference_at(
                identity_uuid,
                registration.effective_at,
                registration.recorded_at - dt.timedelta(microseconds=1),
            )
        )
        self.assertEqual(
            source.reference_at(
                identity_uuid,
                registration.effective_at,
                known_after,
            ),
            reference,
        )

    def test_registration_uses_one_database_receipt_for_effective_and_received_times(self):
        """W-RT1: the act, acceptance, and transitions share the database receipt coordinate."""
        before_receipt = self.database_clock()
        registration = self.register(terms_version=self.terms_version)
        receipt = RegistrationReceipt.objects.get(
            pk=registration.registration_receipt_id
        )
        acceptance = PlatformTermsAcceptance.objects.get(
            acceptance_uuid=registration.evidence_reference.removeprefix(
                "platform-terms-acceptance:"
            )
        )
        transitions = list(
            IdentityTransition.objects.filter(
                identity_id=registration.identity_id
            ).order_by("id")
        )

        self.assertLessEqual(before_receipt, receipt.received_at)
        self.assertEqual(registration.received_at, receipt.received_at)
        self.assertEqual(registration.effective_at, receipt.received_at)
        self.assertEqual(acceptance.received_at, receipt.received_at)
        self.assertEqual(acceptance.accepted_at, receipt.received_at)
        self.assertGreaterEqual(registration.recorded_at, receipt.received_at)
        self.assertEqual(
            [(transition.effective_at, transition.received_at) for transition in transitions],
            [(receipt.received_at, receipt.received_at)] * 2,
        )

    def test_registration_returns_not_found_at_receipt_refusal_details(self):
        """The submitted version and the current version are returned on not-found refusal."""
        before = self.table_counts(self.registration_tables)
        requested_version_uuid = uuid4()
        username = f"p1-not-found-terms-{uuid4().hex}"
        with self.assertRaises(RegistrationServiceRefused) as refused:
            self.register(
                username=username,
                terms_version_uuid=requested_version_uuid,
            )

        self.assertEqual(
            refused.exception.code,
            "REG_TERMS_VERSION_NOT_IN_FORCE",
        )
        self.assertEqual(refused.exception.reason, "NOT_FOUND_AT_RECEIPT")
        self.assertIsNone(refused.exception.submitted_version)
        self.assertEqual(
            refused.exception.submitted_version_uuid,
            requested_version_uuid,
        )
        self.assertIn(str(requested_version_uuid), str(refused.exception))
        self.assertEqual(refused.exception.current_version, self.terms_version)
        self.assertEqual(
            str(refused.exception).split(
                "\nRegistration receipt diagnostics:",
                maxsplit=1,
            )[0],
            "Registration was not completed and nothing was recorded: no account "
            "was created and no acceptance was saved. INTEVIA could not find the "
            "platform terms your registration referred to among the terms known "
            "when it received your registration. Please read the terms now in "
            "force and accept them to register.",
        )
        self.assert_receipt_diagnostics(refused.exception, None)
        self.assertEqual(self.table_counts(self.registration_tables), before)
        self.assertFalse(User.objects.filter(username=username).exists())
        self.register(username=username, terms_version=self.terms_version)
        self.assertTrue(User.objects.filter(username=username).exists())

    def test_registration_returns_none_in_force_refusal_details(self):
        """The no-current-terms reason is distinct and rolls its receipt back."""
        before = self.table_counts(self.registration_tables)
        username = f"p1-no-terms-{uuid4().hex}"
        with patch.object(
            LearnerRegistrationService,
            "_latest_terms_version",
            return_value=None,
        ):
            with self.assertRaises(RegistrationServiceRefused) as refused:
                LearnerRegistrationService.register(
                    username=username,
                    password=self.registration_password,
                    display_name="No terms in force",
                    terms_version_uuid=None,
                    terms_accepted=True,
                    correlation_id=uuid4(),
                    entry_mode=LearnerRegistration.EntryMode.SUPPORTED,
                    enterer_executor="test:registration_fixture",
                )

        self.assertEqual(
            refused.exception.code,
            "REG_TERMS_VERSION_NOT_IN_FORCE",
        )
        self.assertEqual(refused.exception.reason, "NONE_IN_FORCE")
        self.assertIsNone(refused.exception.submitted_version)
        self.assertIsNone(refused.exception.submitted_version_uuid)
        self.assertIsNone(refused.exception.current_version)
        self.assertEqual(
            str(refused.exception),
            "Registration was not completed and nothing was recorded: no account "
            "was created and no acceptance was saved. No platform terms were in "
            "force when INTEVIA received your registration, so registration is "
            "not available yet. Please try again later.",
        )
        self.assertEqual(self.table_counts(self.registration_tables), before)
        self.assertFalse(User.objects.filter(username=username).exists())
        self.register(username=username, terms_version=self.terms_version)
        self.assertTrue(User.objects.filter(username=username).exists())

    def test_registration_returns_not_yet_in_force_reason_and_versions(self):
        """W-RT3: a declared future effective time produces the not-yet-in-force reason."""
        future = self.database_clock() + dt.timedelta(days=1)
        version = self.create_terms_version(
            version_label="FIXTURE-FUTURE",
            effective_from=future,
        )
        version.refresh_from_db()
        before = self.table_counts(self.registration_tables)

        with self.assertRaises(RegistrationServiceRefused) as refused:
            register_test_learner(
                username=f"p1-not-yet-effective-{uuid4().hex}",
                password=self.registration_password,
                display_name="P1 future terms refusal",
                terms_version=version,
            )

        self.assertEqual(refused.exception.reason, "NOT_YET_IN_FORCE")
        self.assertEqual(refused.exception.submitted_version, version)
        self.assertEqual(refused.exception.current_version, self.terms_version)
        self.assertTrue(
            str(refused.exception).startswith(
                "Registration was not completed and nothing was recorded: no account "
                "was created and no acceptance was saved. When INTEVIA received your "
                "registration, the platform terms you accepted (FIXTURE-FUTURE) had "
                "not yet come into force. Please read the terms now in force and "
                "accept them to register.\n"
                "Registration receipt diagnostics:"
            )
        )
        self.assert_receipt_diagnostics(refused.exception, version)
        self.assertEqual(self.table_counts(self.registration_tables), before)

    def test_registration_accepts_the_terms_version_in_force(self):
        """W-A1.9 - SELF service: acceptance and registration cite the current platform terms."""
        registration = self.register()
        acceptance = PlatformTermsAcceptance.objects.select_related(
            "terms_version"
        ).get(acceptance_uuid=registration.evidence_reference.removeprefix(
            "platform-terms-acceptance:"
        ))

        self.assertEqual(
            acceptance.acceptance_kind,
            PlatformTermsAcceptance.Kind.REGISTRATION,
        )
        self.assertEqual(
            acceptance.subject_reference,
            f"learner-registration:{registration.registration_uuid}",
        )
        self.assertEqual(
            acceptance.accepted_by_identity_id,
            registration.identity_id,
        )
        self.assertEqual(
            acceptance.accepted_by_access_epoch,
            registration.actor_access_epoch,
        )
        self.assertEqual(
            registration.authority_reference,
            f"platform-terms-version:{acceptance.terms_version.terms_version_uuid}"
            "#registration",
        )
        self.assertEqual(
            LearnerRegistrationService._current_terms_version(
                terms_version_uuid=acceptance.terms_version.terms_version_uuid,
                effective_at=registration.effective_at,
                received_at=registration.received_at,
            ),
            acceptance.terms_version,
        )
        self.assertEqual(
            acceptance.terms_version.version_label,
            "FIXTURE-DEV-1",
        )

    def test_registration_refuses_missing_terms_acceptance_without_writes(self):
        """REG_TERMS_NOT_ACCEPTED - SELF service: an unchecked terms form writes nothing."""
        before = self.table_counts(self.registration_tables)
        with self.assertRaises(RegistrationServiceRefused) as refused:
            self.register(terms_accepted=False)

        self.assertEqual(refused.exception.code, "REG_TERMS_NOT_ACCEPTED")
        self.assertTrue(
            str(refused.exception).startswith(
            "Registration was not completed and no account was created. "
            "INTEVIA records that you accepted the platform terms in force, "
            "and no acceptance was received. Read the terms shown and confirm "
            "your acceptance to continue."
            )
        )
        self.assertEqual(self.table_counts(self.registration_tables), before)

    def test_registration_refuses_terms_superseded_at_receipt_and_returns_current(self):
        """W-RT2: changed terms refuse without writes and return the version to accept."""
        successor = self.create_terms_version(
            version_label="FIXTURE-DEV-2",
            supersedes=self.terms_version,
        )
        successor.refresh_from_db()
        before = self.table_counts(self.registration_tables)
        username = f"p1-superseded-terms-{uuid4().hex}"

        with self.assertRaises(RegistrationServiceRefused) as refused:
            self.register(username=username, terms_version=self.terms_version)

        self.assertEqual(refused.exception.reason, "SUPERSEDED_AT_RECEIPT")
        self.assertEqual(refused.exception.submitted_version, self.terms_version)
        self.assertEqual(refused.exception.current_version, successor)
        self.assertTrue(
            str(refused.exception).startswith(
                "Registration was not completed and nothing was recorded: no account "
                "was created and no acceptance was saved. When INTEVIA received your "
                "registration, the platform terms you accepted (FIXTURE-DEV-1) were "
                "no longer in force; FIXTURE-DEV-2 was in force instead. Your "
                "acceptance has not been applied to the newer terms. Please read "
                "the terms now in force and accept them to register.\n"
                "Registration receipt diagnostics:"
            )
        )
        self.assert_receipt_diagnostics(refused.exception, self.terms_version)
        self.assertEqual(self.table_counts(self.registration_tables), before)
        self.assertFalse(User.objects.filter(username=username).exists())

        accepted = self.register(
            username=username,
            terms_version=successor,
        )
        acceptance = PlatformTermsAcceptance.objects.get(
            acceptance_uuid=accepted.evidence_reference.removeprefix(
                "platform-terms-acceptance:"
            )
        )
        self.assertEqual(acceptance.terms_version_id, successor.pk)

    def test_registration_refuses_superseded_chain_and_returns_latest_version(self):
        """W-RT4: a chain returns its latest effective and known successor."""
        second = self.create_terms_version(
            version_label="FIXTURE-DEV-2",
            supersedes=self.terms_version,
        )
        third = self.create_terms_version(
            version_label="FIXTURE-DEV-3",
            supersedes=second,
        )
        before = self.table_counts(self.registration_tables)

        with self.assertRaises(RegistrationServiceRefused) as refused:
            self.register(
                username=f"p1-old-chain-terms-{uuid4().hex}",
                terms_version=self.terms_version,
            )

        self.assertEqual(refused.exception.reason, "SUPERSEDED_AT_RECEIPT")
        self.assertEqual(refused.exception.submitted_version, self.terms_version)
        self.assertEqual(refused.exception.current_version, third)
        self.assertEqual(self.table_counts(self.registration_tables), before)

    def test_registration_uses_database_receipt_despite_application_clock_skew(self):
        """W-RT5: application-clock offsets do not set or reject the receipt coordinate."""
        for offset in (-2, 2):
            with self.subTest(offset_seconds=offset):
                before_receipt = self.database_clock()
                application_clock = before_receipt + dt.timedelta(seconds=offset)
                with patch(
                    "src.intevia.services.identity_service.timezone.now",
                    return_value=application_clock,
                ):
                    registration = self.register(
                        username=f"p1-clock-skew-{offset}-{uuid4().hex}",
                        terms_version=self.terms_version,
                    )
                after_receipt = self.database_clock()
                receipt = RegistrationReceipt.objects.get(
                    pk=registration.registration_receipt_id
                )
                self.assertLessEqual(before_receipt, receipt.received_at)
                self.assertLessEqual(receipt.received_at, after_receipt)
                self.assertNotEqual(receipt.received_at, application_clock)
                self.assertEqual(registration.effective_at, receipt.received_at)
                self.assertEqual(registration.received_at, receipt.received_at)

    def test_registration_version_inserted_after_receipt_does_not_change_judgment(self):
        """W-RT6: the guard judges the submitted version at the captured receipt."""
        original_create = PlatformTermsAcceptance.objects.create
        successors = []

        def create_acceptance_then_successor(*args, **kwargs):
            acceptance = original_create(*args, **kwargs)
            if not successors:
                successors.append(
                    self.create_terms_version(
                        version_label="FIXTURE-DEV-AFTER-RECEIPT",
                        effective_from=self.terms_version.effective_from,
                        supersedes=self.terms_version,
                    )
                )
            return acceptance

        with patch.object(
            PlatformTermsAcceptance.objects,
            "create",
            side_effect=create_acceptance_then_successor,
        ):
            registration = self.register(
                username=f"p1-successor-after-receipt-{uuid4().hex}",
                terms_version=self.terms_version,
            )

        successor = successors[0]
        successor.refresh_from_db()
        self.assertGreater(successor.recorded_at, registration.received_at)
        acceptance = PlatformTermsAcceptance.objects.get(
            acceptance_uuid=registration.evidence_reference.removeprefix(
                "platform-terms-acceptance:"
            )
        )
        self.assertEqual(acceptance.terms_version_id, self.terms_version.pk)
        self.assertEqual(registration.effective_at, registration.received_at)

    def test_registration_receipt_is_database_assigned_immutable_and_first(self):
        """W-RT8: receipt assignment and immutability are database enforced; order is service behaviour."""
        for column, value in (
            ("received_at", self.database_clock()),
            ("transaction_id", 1),
        ):
            with self.subTest(column=column), self.assertRaises(DatabaseError):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO public.core_registrationreceipt "
                        f"({column}) VALUES (%s)",
                        [value],
                    )

        writes = []

        def capture_writes(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith(
                ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE ")
            ):
                writes.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(capture_writes):
            registration = self.register(
                username=f"p1-receipt-order-{uuid4().hex}",
                terms_version=self.terms_version,
            )

        self.assertTrue(writes)
        self.assertIn("core_registrationreceipt", writes[0].lower())
        receipt = RegistrationReceipt.objects.get(
            pk=registration.registration_receipt_id
        )

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                RegistrationReceipt.objects.filter(pk=receipt.pk).update(
                    received_at=receipt.received_at + dt.timedelta(seconds=1)
                )
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                RegistrationReceipt.objects.filter(pk=receipt.pk).delete()
        with self.assertRaises(DatabaseError):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("TRUNCATE TABLE public.core_registrationreceipt")

        self.assertTrue(RegistrationReceipt.objects.filter(pk=receipt.pk).exists())

    def test_registration_rejects_validator_password_without_writes(self):
        """REG_PASSWORD_REJECTED - SELF service: Django's validator reason is returned before writes."""
        rejected_password = "x"
        before = self.table_counts(self.registration_tables)
        with self.assertRaises(ValidationError) as validation:
            validate_password(rejected_password, user=User(username="new-person"))
        with self.assertRaises(RegistrationServiceRefused) as refused:
            self.register(password=rejected_password)

        self.assertEqual(refused.exception.code, "REG_PASSWORD_REJECTED")
        self.assertTrue(
            str(refused.exception).startswith(
            "Registration was not completed and no account was created. "
            "The password did not meet INTEVIA's password requirements: "
            f"{'; '.join(validation.exception.messages)}. Choose a password "
            "that meets them."
            )
        )
        self.assertEqual(self.table_counts(self.registration_tables), before)

    def test_fixture_terms_are_refused_on_a_non_disposable_database(self):
        """W-P1.F1 - SELF route: fixture terms refuse outside an attempt-owned database."""
        before = self.table_counts(self.registration_tables)
        with patch.dict(connection.settings_dict, {"NAME": "intevia-production"}):
            self.assertFalse(fixture_terms_permitted("default"))
            with self.assertRaises(RegistrationServiceRefused) as refused:
                self.register()

        self.assertEqual(refused.exception.code, "REG_TERMS_TEST_ONLY")
        self.assertTrue(
            str(refused.exception).startswith(
            "Registration was not completed and no account was created. "
            "The platform terms set up here are a test-only version, which "
            "cannot be used to register a real account. Nothing can be done "
            "from this page; the platform's operators must publish the real "
            "platform terms first. (Operator: use a disposable development "
            "database, or publish a real terms version.)"
            )
        )
        self.assertEqual(self.table_counts(self.registration_tables), before)

    def test_fixture_terms_are_refused_on_protected_database_names(self):
        """W-P1.F2 - SELF route: fixture terms refuse on both protected names."""
        before = self.table_counts(self.registration_tables)
        for database_name in ("intevia", "intevia_dev"):
            with self.subTest(database_name=database_name):
                with patch.dict(
                    connection.settings_dict,
                    {"NAME": database_name},
                ):
                    self.assertFalse(fixture_terms_permitted("default"))
                    with self.assertRaises(RegistrationServiceRefused) as refused:
                        self.register()
                self.assertEqual(
                    refused.exception.code,
                    "REG_TERMS_TEST_ONLY",
                )
                self.assertEqual(
                    self.table_counts(self.registration_tables),
                    before,
                )

    def test_fixture_terms_are_cited_on_disposable_registration(self):
        """W-P1.F3 - SELF route: fixture terms are permitted only in the disposable attempt database."""
        self.assertTrue(fixture_terms_permitted("default"))
        registration = self.register()
        acceptance = PlatformTermsAcceptance.objects.select_related(
            "terms_version"
        ).get(
            acceptance_uuid=registration.evidence_reference.removeprefix(
                "platform-terms-acceptance:"
            )
        )

        self.assertEqual(
            acceptance.terms_version.version_label,
            "FIXTURE-DEV-1",
        )
        self.assertEqual(
            registration.authority_reference,
            f"platform-terms-version:{acceptance.terms_version.terms_version_uuid}"
            "#registration",
        )

    def test_self_registration_creates_no_membership_in_any_living_organism(self):
        """W-P1.M1 - SELF route: all fifteen LO-membership tables and own-membership read stay unchanged."""
        before = self.table_counts(self.membership_tables)
        registration = self.register()
        identity = Identity.objects.get(pk=registration.identity_id)

        self.assertEqual(
            self.table_counts(self.membership_tables),
            before,
        )
        self.assertEqual(OrganismMembershipReader().list_own_memberships(identity), [])

    def test_username_is_stored_once_with_the_canonical_identity_link(self):
        """W-P1.U1 - SELF route: one Django username resolves through the canonical identity link."""
        username = f"P1-Username-{uuid4().hex}"
        registration = self.register(username=username)
        identity = Identity.objects.select_related("credential").get(
            pk=registration.identity_id
        )

        self.assertEqual(User.objects.filter(username=username).count(), 1)
        self.assertEqual(
            Identity.objects.filter(
                canonical_username=canonical_username_v1(username)
            ).count(),
            1,
        )
        self.assertEqual(
            self.resolution_for(identity),
            (canonical_username_v1(username), identity.credential_id),
        )
        self.assertEqual(
            registration.entry_mode,
            LearnerRegistration.EntryMode.SELF,
        )

    def test_unavailable_username_is_refused_without_writes(self):
        """W-P1.U3 - SELF route: an occupied username is refused without a partial account."""
        username = f"P1-Occupied-{uuid4().hex}"
        self.register(username=username)
        before = self.table_counts(self.registration_tables)
        with self.assertRaises(RegistrationServiceRefused) as refused:
            self.register(username=username.lower())

        self.assertEqual(refused.exception.code, "REG_USERNAME_UNAVAILABLE")
        self.assertTrue(
            str(refused.exception).startswith(
            "Registration was not completed and no account was created. "
            "The username you chose is already in use. Choose a different "
            "username and submit again."
            )
        )
        self.assertEqual(self.table_counts(self.registration_tables), before)

    def test_supported_provisioning_records_the_person_as_actor(self):
        username = f"P1-Supported-{uuid4().hex}"
        before_membership = self.table_counts(self.membership_tables)
        output = io.StringIO()
        with patch("builtins.input", return_value="ACCEPT"), patch(
            "getpass.getpass",
            side_effect=[self.registration_password, self.registration_password],
        ):
            call_command(
                "provision_learner",
                username=username,
                display_name="Supported P1 Learner",
                terms_version=self.terms_version.version_label,
                stdout=output,
            )

        identity = Identity.objects.select_related("credential").get(
            credential__username=username
        )
        registration = LearnerRegistration.objects.get(identity=identity)
        transitions = list(
            IdentityTransition.objects.filter(identity=identity).order_by(
                "occurred_at", "pk"
            )
        )
        self.assertEqual(
            registration.entry_mode,
            LearnerRegistration.EntryMode.SUPPORTED,
        )
        self.assertEqual(
            registration.enterer_executor,
            "management:provision_learner",
        )
        self.assertEqual(
            [transition.action for transition in transitions],
            [
                IdentityTransition.Action.PROVISION,
                IdentityTransition.Action.ACTIVATE,
            ],
        )
        for transition in transitions:
            self.assertEqual(transition.requesting_actor_id, identity.pk)
            self.assertEqual(
                transition.authority_reference,
                f"learner-registration:{registration.registration_uuid}",
            )
        self.assertFalse(identity.originating_membership_requests.exists())
        self.assertEqual(self.table_counts(self.membership_tables), before_membership)
        self.assertEqual(OrganismMembershipReader().list_own_memberships(identity), [])
        self.assertIn(str(registration.registration_uuid), output.getvalue())

    def test_supported_provisioning_refuses_test_terms_outside_permitted_names(self):
        before = self.table_counts(self.registration_tables)
        for database_name in ("development-live", "intevia", "intevia_dev"):
            with self.subTest(database_name=database_name):
                output = io.StringIO()
                with patch.dict(
                    connections["default"].settings_dict,
                    {"NAME": database_name},
                ), patch("builtins.input", return_value="ACCEPT"), patch(
                    "getpass.getpass",
                    side_effect=[
                        self.registration_password,
                        self.registration_password,
                    ],
                ):
                    with self.assertRaises(CommandError) as refused:
                        call_command(
                            "provision_learner",
                            username=f"P1-Unsupported-{uuid4().hex}",
                            display_name="Unsupported P1 Learner",
                            terms_version=self.terms_version.version_label,
                            stdout=output,
                        )
                self.assertEqual(
                    str(refused.exception),
                    "Registration was not completed and no account was created. "
                    "The platform terms set up here are a test-only version, "
                    "which cannot be used to register a real account. Nothing "
                    "can be done from this page; the platform's operators must "
                    "publish the real platform terms first. (Operator: use a "
                    "disposable development database, or publish a real terms "
                    "version.)",
                )
                self.assertEqual(self.table_counts(self.registration_tables), before)

    def test_sign_in_requires_registration_and_binds_the_registered_username(self):
        username = f"P1-Sign-In-{uuid4().hex}"
        registration = self.register(username=username)
        identity = Identity.objects.select_related("credential").get(
            pk=registration.identity_id
        )
        client = Client()
        response = client.post(
            reverse("login"),
            {"username": username, "password": self.registration_password},
        )
        self.assertTrue(response.url.startswith(reverse("shell")))
        self.assertEqual(
            client.session[SESSION_IDENTITY_KEY],
            str(identity.identity_id),
        )
        self.assertEqual(
            client.session["_auth_user_id"],
            str(identity.credential_id),
        )
        self.assertEqual(
            self.resolution_for(identity),
            (identity.canonical_username, identity.credential_id),
        )

        from core.views import SIGNIN_UNAVAILABLE

        operator_message = (
            "Sign-in was refused because no governed registration effective and "
            "known at sign-in was found for this identity. Check how the account "
            "was created and whether registration completed. Resolve it through "
            "the supported registration process; do not bypass the registration "
            "check."
        )

        wrong_password_response = Client().post(
            reverse("login"),
            {
                "username": username,
                "password": "incorrect-test-password",
            },
        )
        public_messages = [
            [
                str(message)
                for message in wrong_password_response.context["form"].non_field_errors()
            ]
        ]
        for state in (Identity.AccessState.ACTIVE, Identity.AccessState.RESTRICTED):
            with self.subTest(state=state):
                credential = User.objects.create_user(
                    username=f"P1-Unregistered-{uuid4().hex}",
                    password=self.registration_password,
                )
                unregistered = create_test_identity(
                    credential=credential,
                    display_name="Unregistered P1 Identity",
                    access_state=state,
                )
                unregistered_client = Client()
                with self.assertLogs("intevia.signin", level="WARNING") as logged:
                    refused = unregistered_client.post(
                        reverse("login"),
                        {
                            "username": credential.username,
                            "password": self.registration_password,
                        },
                    )
                self.assertContains(refused, SIGNIN_UNAVAILABLE)
                self.assertNotIn("_auth_user_id", unregistered_client.session)
                self.assertNotIn(SESSION_IDENTITY_KEY, unregistered_client.session)
                self.assertNotIn(
                    SESSION_ACCESS_EPOCH_KEY, unregistered_client.session
                )
                self.assertNotIn(
                    SESSION_STARTED_AT_KEY, unregistered_client.session
                )
                self.assertEqual(len(logged.records), 1)
                record = logged.records[0]
                self.assertEqual(record.name, "intevia.signin")
                self.assertEqual(record.levelname, "WARNING")
                self.assertEqual(
                    record.args,
                    (
                        "SIGNIN_REGISTRATION_INCOMPLETE",
                        operator_message,
                        unregistered.identity_id,
                    ),
                )
                self.assertEqual(
                    record.getMessage(),
                    "SIGNIN_REGISTRATION_INCOMPLETE | "
                    f"{operator_message} | "
                    f"{unregistered.identity_id}",
                )
                public_messages.append(
                    [
                        str(message)
                        for message in refused.context["form"].non_field_errors()
                    ]
                )

        unknown_client = Client()
        unknown = unknown_client.post(
            reverse("login"),
            {
                "username": f"unknown-{uuid4().hex}",
                "password": self.registration_password,
            },
        )
        self.assertContains(unknown, SIGNIN_UNAVAILABLE)
        self.assertNotIn("_auth_user_id", unknown_client.session)
        public_messages.append(
            [str(message) for message in unknown.context["form"].non_field_errors()]
        )
        self.assertEqual(public_messages, [[SIGNIN_UNAVAILABLE]] * 4)

    def test_registered_sign_in_succeeds_with_application_clock_behind(self):
        """W-SG2: a two-second application-clock lag does not cause a false refusal."""
        registration = self.register(
            username=f"P1-Sign-In-Clock-{uuid4().hex}"
        )
        identity = Identity.objects.select_related("credential").get(
            pk=registration.identity_id
        )
        before_signin = self.database_clock()
        application_clock = before_signin - dt.timedelta(seconds=2)
        clock_reads = []

        def record_clock_read(execute, sql, params, many, context):
            if "SELECT clock_timestamp()" in sql:
                clock_reads.append(sql)
            return execute(sql, params, many, context)

        client = Client()
        with patch("django.utils.timezone.now", return_value=application_clock):
            with connection.execute_wrapper(record_clock_read):
                with self.assertNoLogs("intevia.signin", level="WARNING"):
                    response = client.post(
                        reverse("login"),
                        {
                            "username": identity.credential.username,
                            "password": self.registration_password,
                        },
                    )

        self.assertTrue(response.url.startswith(reverse("shell")))
        self.assertEqual(len(clock_reads), 1)
        self.assertEqual(
            client.session[SESSION_IDENTITY_KEY],
            str(identity.identity_id),
        )

    def test_sign_in_unavailable_message_is_identical_for_all_four_conditions(self):
        """W-A1.14: all four sign-in refusals present the same message."""
        from core.views import SIGNIN_UNAVAILABLE

        registered = self.register(username=f"P1-Sign-In-Message-{uuid4().hex}")
        registered_identity = Identity.objects.get(pk=registered.identity_id)
        wrong_password_response = Client().post(
            reverse("login"),
            {
                "username": registered_identity.credential.username,
                "password": "incorrect-test-password",
            },
        )

        unknown_response = Client().post(
            reverse("login"),
            {
                "username": f"P1-Unknown-{uuid4().hex}",
                "password": self.registration_password,
            },
        )

        pending_credential = User.objects.create_user(
            username=f"P1-Pending-Sign-In-{uuid4().hex}",
            password=self.registration_password,
        )
        create_test_identity(
            credential=pending_credential,
            display_name="Pending Sign-In Message",
            access_state=Identity.AccessState.PENDING,
        )
        pending_response = Client().post(
            reverse("login"),
            {
                "username": pending_credential.username,
                "password": self.registration_password,
            },
        )

        deactivated_registration = self.register(
            username=f"P1-Deactivated-Sign-In-{uuid4().hex}"
        )
        deactivated = Identity.objects.get(pk=deactivated_registration.identity_id)
        IdentityLifecycleService.change_access(
            identity_id=deactivated.identity_id,
            action=IdentityTransition.Action.DEACTIVATE,
            requesting_actor=deactivated,
            authority_reference="authority:signin-message-deactivate",
            technical_executor="test-suite",
            evidence_reference="evidence:signin-message-deactivate",
            reason_category=IdentityTransition.ReasonCategory.SECURITY,
            correlation_id=uuid4(),
        )
        deactivated_response = Client().post(
            reverse("login"),
            {
                "username": deactivated.credential.username,
                "password": self.registration_password,
            },
        )

        responses = (
            wrong_password_response,
            unknown_response,
            pending_response,
            deactivated_response,
        )
        messages = [
            [str(message) for message in response.context["form"].non_field_errors()]
            for response in responses
        ]
        self.assertEqual(messages, [[SIGNIN_UNAVAILABLE]] * 4)

    def test_admin_login_records_current_admin_login_limitation(self):
        for next_path, protected_marker in (
            (reverse("shell"), "Your INTEVIA home"),
            ("/admin/", "Site administration"),
        ):
            with self.subTest(next_path=next_path):
                username = f"admin-boundary-{uuid4().hex}"
                User.objects.create_user(
                    username=username,
                    password=self.registration_password,
                    is_staff=True,
                )
                client = Client()
                login_response = client.post(
                    "/admin/login/",
                    {
                        "username": username,
                        "password": self.registration_password,
                    },
                )
                self.assertEqual(login_response.status_code, 302)
                self.assertIn("_auth_user_id", client.session)

                response = client.get(next_path)

                self.assertEqual(response.status_code, 302)
                self.assertNotIn(
                    protected_marker,
                    response.content.decode("utf-8"),
                )
                self.assertNotIn("_auth_user_id", client.session)

    def test_registration_has_no_production_path_through_the_old_root_gate(self):
        """W-A1.15: registration entrypoints never invoke the retired root-LO path."""
        import inspect

        from core.management.commands.provision_learner import Command

        registration_sources = (
            inspect.getsource(LearnerRegistrationService.register),
            inspect.getsource(LearnerRegistrationService._register),
            inspect.getsource(Command.handle),
        )
        self.assertIn(
            "LearnerRegistrationService.register",
            registration_sources[2],
        )
        for source in registration_sources:
            for legacy_reference in (
                "OriginatingMembershipProvisioningRequest",
                "originating_membership_requests",
                "_fulfilled_request",
                "root-LO",
            ):
                self.assertNotIn(legacy_reference, source)

    def test_registered_learner_name_resolves_through_application_reader(self):
        from src.intevia.services.organism_application_authority import (
            ProductionApplicationAuthorityProvider,
        )
        from src.intevia.services.organism_application_reads import (
            OrganismApplicationReader,
        )
        from src.intevia.services.organism_authority import S015Authority

        display_name = "Registered Name Resolution"
        registration = self.register(display_name=display_name)
        identity = Identity.objects.get(pk=registration.identity_id)
        reader = OrganismApplicationReader(
            authority=S015Authority(
                provider=ProductionApplicationAuthorityProvider()
            ),
            clock=lambda: dt.datetime.now(dt.timezone.utc),
        )

        resolved = reader._actor_ref(identity.pk)

        self.assertEqual(resolved.resolution, "RESOLVED")
        self.assertEqual(resolved.display_name, display_name)

    def test_username_is_fixed_and_display_name_changes_resolve(self):
        from src.intevia.services.organism_application_authority import (
            ProductionApplicationAuthorityProvider,
        )
        from src.intevia.services.organism_application_reads import (
            OrganismApplicationReader,
        )
        from src.intevia.services.organism_authority import S015Authority

        username = f"P1-Fixed-{uuid4().hex}"
        registration = self.register(username=username)
        identity = Identity.objects.select_related("credential").get(
            pk=registration.identity_id
        )
        credential_id = identity.credential_id
        canonical_username = identity.canonical_username
        reader = OrganismApplicationReader(
            authority=S015Authority(
                provider=ProductionApplicationAuthorityProvider()
            ),
            clock=lambda: dt.datetime.now(dt.timezone.utc),
        )
        registration_before = LearnerRegistration.objects.filter(
            pk=registration.pk
        ).values().get()
        before = self.table_counts(self.registration_tables)
        with self.assertRaises(CredentialServiceRefused) as refused:
            CredentialService.update_username(
                identity_id=identity.identity_id,
                username=f"renamed-{uuid4().hex}",
                requesting_actor=identity,
                authority_reference="authority:username-change",
                evidence_reference="evidence:username-change",
                correlation_id=uuid4(),
                technical_executor="test-suite",
            )
        self.assertEqual(refused.exception.code, "USERNAME_CHANGE_UNAVAILABLE")
        self.assertEqual(
            refused.exception.message,
            "Your username was not changed. Usernames in INTEVIA are fixed once "
            "registered, because your records rely on them staying the same. "
            "Your display name can still be changed.",
        )
        self.assertEqual(self.table_counts(self.registration_tables), before)

        identity.display_name = "Changed P1 Display Name"
        identity.save(update_fields=("display_name", "updated_at"))
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.core_identityresolution SET display_name = %s "
                "WHERE identity_id = %s",
                [identity.display_name, identity.pk],
            )
        identity.refresh_from_db()
        self.assertEqual(identity.display_name, "Changed P1 Display Name")
        changed_name = reader._actor_ref(identity.pk)
        self.assertEqual(changed_name.resolution, "RESOLVED")
        self.assertEqual(changed_name.display_name, "Changed P1 Display Name")
        self.assertEqual(identity.credential.username, username)
        self.assertEqual(identity.credential_id, credential_id)
        self.assertEqual(identity.canonical_username, canonical_username)
        self.assertEqual(
            self.resolution_for(identity),
            (identity.canonical_username, identity.credential_id),
        )
        self.assertEqual(
            LearnerRegistration.objects.filter(pk=registration.pk).values().get(),
            registration_before,
        )
        self.assertEqual(self.table_counts(self.registration_tables), before)

    def test_name_takedown_severs_name_and_preserves_registration_history(self):
        from src.intevia.services.organism_application_authority import (
            ProductionApplicationAuthorityProvider,
        )
        from src.intevia.services.organism_application_reads import (
            OrganismApplicationReader,
        )
        from src.intevia.services.organism_authority import S015Authority

        username = f"P1-Takedown-{uuid4().hex}"
        registration = self.register(username=username)
        identity = Identity.objects.select_related("credential").get(
            pk=registration.identity_id
        )
        reader = OrganismApplicationReader(
            authority=S015Authority(
                provider=ProductionApplicationAuthorityProvider()
            ),
            clock=lambda: dt.datetime.now(dt.timezone.utc),
        )
        registration_before = LearnerRegistration.objects.filter(
            pk=registration.pk
        ).values().get()
        acceptance = PlatformTermsAcceptance.objects.get(
            accepted_by_identity=identity,
            acceptance_kind=PlatformTermsAcceptance.Kind.REGISTRATION,
        )
        acceptance_before = PlatformTermsAcceptance.objects.filter(
            pk=acceptance.pk
        ).values().get()
        transitions_before = list(
            IdentityTransition.objects.filter(identity=identity)
            .order_by("recorded_at", "pk")
            .values()
        )
        credential_before = User.objects.filter(
            pk=identity.credential_id
        ).values().get()
        before = self.table_counts(self.registration_tables)

        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM public.core_identityresolution "
                "WHERE identity_id = %s",
                [identity.pk],
            )
        taken_down_name = reader._actor_ref(identity.pk)
        self.assertEqual(taken_down_name.resolution, "SEVERED")
        self.assertIsNone(taken_down_name.display_name)
        self.assertIsNone(self.resolution_for(identity))
        self.assertTrue(Identity.objects.filter(pk=identity.pk).exists())
        after_takedown = self.table_counts(self.registration_tables)
        expected_counts = before.copy()
        expected_counts["core_identityresolution"] -= 1
        self.assertEqual(after_takedown, expected_counts)
        self.assertTrue(
            LearnerRegistration.objects.filter(pk=registration.pk).values().get()
            == registration_before
        )
        self.assertTrue(
            PlatformTermsAcceptance.objects.filter(pk=acceptance.pk).values().get()
            == acceptance_before
        )
        self.assertTrue(
            list(
                IdentityTransition.objects.filter(identity=identity)
                .order_by("recorded_at", "pk")
                .values()
            )
            == transitions_before
        )
        credential_after = User.objects.filter(
            pk=identity.credential_id
        ).values().get()
        self.assertTrue(credential_after == credential_before)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT identity_id, severed_at "
                "FROM public.core_identityresolutionsevered "
                "WHERE identity_id = %s",
                [identity.pk],
            )
            severance = cursor.fetchone()
        self.assertIsNotNone(severance)
        self.assertEqual(severance[0], identity.pk)
        self.assertIsNotNone(severance[1])
        with self.assertRaises(RegistrationServiceRefused) as unavailable:
            self.register(username=username)
        self.assertEqual(unavailable.exception.code, "REG_USERNAME_UNAVAILABLE")
        self.assertEqual(self.table_counts(self.registration_tables), after_takedown)

    def test_reactivation_requires_proof_and_retains_one_registration(self):
        registration = self.register()
        identity = Identity.objects.select_related("credential").get(
            pk=registration.identity_id
        )
        registration_before = LearnerRegistration.objects.filter(
            pk=registration.pk
        ).values().get()
        membership_before = self.table_counts(self.membership_tables)
        transitions_before = IdentityTransition.objects.filter(
            identity=identity
        ).count()
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
        self.assertEqual(identity.access_state, Identity.AccessState.DEACTIVATED)
        self.assertEqual(
            IdentityTransition.objects.filter(identity=identity).count(),
            transitions_before + 1,
        )

        with self.assertRaises(IdentityLifecycleRefused) as refused:
            IdentityLifecycleService.change_access(
                identity_id=identity.identity_id,
                action=IdentityTransition.Action.REACTIVATE,
                requesting_actor=identity,
                authority_reference="authority:reactivate",
                technical_executor="test-suite",
                evidence_reference="evidence:reactivate",
                reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
                correlation_id=uuid4(),
                proof_password="incorrect-test-password",
            )
        self.assertEqual(refused.exception.code, "REACT_PROOF_OF_CONTROL_REQUIRED")
        identity.refresh_from_db()
        self.assertEqual(identity.access_state, Identity.AccessState.DEACTIVATED)
        self.assertEqual(
            IdentityTransition.objects.filter(identity=identity).count(),
            transitions_before + 1,
        )

        correlation_id = uuid4()
        transition = IdentityLifecycleService.change_access(
            identity_id=identity.identity_id,
            action=IdentityTransition.Action.REACTIVATE,
            requesting_actor=None,
            authority_reference="authority:reactivate",
            technical_executor="test-suite",
            evidence_reference="evidence:reactivate",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=correlation_id,
            proof_password=self.registration_password,
        )
        identity.refresh_from_db()
        identity.credential.refresh_from_db()
        self.assertEqual(identity.access_state, Identity.AccessState.ACTIVE)
        self.assertTrue(identity.credential.is_active)
        self.assertEqual(identity.access_epoch, 2)
        self.assertEqual(
            IdentityTransition.objects.filter(identity=identity).count(),
            transitions_before + 2,
        )
        self.assertEqual(
            transition.authority_reference,
            f"learner-registration:{registration.registration_uuid}",
        )
        self.assertEqual(
            transition.evidence_reference,
            f"reauthentication:password:{correlation_id}",
        )
        self.assertEqual(transition.requesting_actor_id, identity.pk)
        self.assertEqual(
            LearnerRegistration.objects.filter(identity=identity).count(),
            1,
        )
        self.assertEqual(
            LearnerRegistration.objects.filter(pk=registration.pk).values().get(),
            registration_before,
        )
        self.assertFalse(identity.originating_membership_requests.exists())
        self.assertEqual(self.table_counts(self.membership_tables), membership_before)
        at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)
        self.assertEqual(
            identity_state_at(
                identity.identity_id,
                state_at=at,
                known_at=at,
            ).access_epoch,
            identity.access_epoch,
        )

    def test_temporal_identity_reader_derives_epoch_across_late_transitions(self):
        registration = self.register()
        identity = Identity.objects.get(pk=registration.identity_id)
        original_activation = IdentityTransition.objects.filter(
            identity=identity,
            action=IdentityTransition.Action.ACTIVATE,
        ).get()
        historical_time = original_activation.occurred_at - dt.timedelta(seconds=1)
        historical = IdentityTransition.objects.create(
            identity=identity,
            action=IdentityTransition.Action.PROVISION,
            prior_state=None,
            resulting_state=Identity.AccessState.PENDING,
            authority_reference="historical-migration-fixture",
            technical_executor="test-suite",
            evidence_reference="historical-migration-fixture",
            reason_category=IdentityTransition.ReasonCategory.PROVISIONING,
            correlation_id=uuid4(),
            occurred_at=historical_time,
        )
        IdentityTransition.objects.filter(pk=historical.pk).update(
            effective_at=None,
            received_at=None,
            recorded_at=None,
        )
        historical_read = identity_state_at(
            identity.identity_id,
            state_at=historical_time,
            known_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1),
        )
        self.assertEqual(historical_read.access_state, Identity.AccessState.PENDING)
        self.assertEqual(historical_read.access_epoch, 0)

        future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
        password = "P1-Temporal-Password-Changed-47!"
        password_transition = CredentialService.change_password(
            identity_id=identity.identity_id,
            new_password=password,
            requesting_actor=identity,
            session_access_epoch=identity.access_epoch,
            correlation_id=uuid4(),
            occurred_at=future,
        )
        identity.refresh_from_db()
        deactivation = IdentityLifecycleService.change_access(
            identity_id=identity.identity_id,
            action=IdentityTransition.Action.DEACTIVATE,
            requesting_actor=identity,
            authority_reference="authority:late-deactivation",
            technical_executor="test-suite",
            evidence_reference="evidence:late-deactivation",
            reason_category=IdentityTransition.ReasonCategory.SECURITY,
            correlation_id=uuid4(),
            occurred_at=future + dt.timedelta(minutes=1),
        )
        reactivation = IdentityLifecycleService.change_access(
            identity_id=identity.identity_id,
            action=IdentityTransition.Action.REACTIVATE,
            requesting_actor=None,
            authority_reference="authority:late-reactivation",
            technical_executor="test-suite",
            evidence_reference="evidence:late-reactivation",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(),
            occurred_at=future + dt.timedelta(minutes=2),
            proof_password=password,
        )
        transitions = list(
            IdentityTransition.objects.filter(identity=identity).order_by("pk")
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT recorded_at FROM public.core_identitytransition "
                "WHERE id = %s",
                [password_transition.pk],
            )
            self.assertEqual(cursor.fetchone()[0], password_transition.recorded_at)
        self.assertIsNotNone(password_transition.recorded_at)
        self.assertIsNotNone(deactivation.recorded_at)
        self.assertIsNotNone(reactivation.recorded_at)

        state_points = set()
        known_points = set()
        delta = dt.timedelta(microseconds=1)
        for item in transitions:
            effective = item.effective_at or item.occurred_at
            received = item.received_at or item.occurred_at
            recorded = item.recorded_at or item.occurred_at
            state_points.update((effective - delta, effective, effective + delta))
            known_points.update((received - delta, received, received + delta))
            known_points.update((recorded - delta, recorded, recorded + delta))

        epoch_actions = {
            IdentityTransition.Action.RESTRICT,
            IdentityTransition.Action.DEACTIVATE,
            IdentityTransition.Action.REACTIVATE,
            IdentityTransition.Action.REPLACE_CREDENTIAL,
            IdentityTransition.Action.RETIRE_CREDENTIAL,
        }
        for state_at in sorted(state_points):
            for known_at in sorted(known_points):
                eligible = [
                    item
                    for item in transitions
                    if (item.effective_at or item.occurred_at) <= state_at
                    and (item.received_at or item.occurred_at) <= known_at
                    and (item.recorded_at or item.occurred_at) <= known_at
                ]
                expected_epoch = sum(
                    item.action in epoch_actions for item in eligible
                )
                expected_latest = max(
                    eligible,
                    key=lambda item: (
                        item.effective_at or item.occurred_at,
                        item.recorded_at or item.occurred_at,
                        item.pk,
                    ),
                    default=None,
                )
                actual = identity_state_at(
                    identity.identity_id,
                    state_at=state_at,
                    known_at=known_at,
                )
                self.assertEqual(actual.access_epoch, expected_epoch)
                self.assertEqual(
                    actual.access_state,
                    None if expected_latest is None else expected_latest.resulting_state,
                )

        late_deactivation = identity_state_at(
            identity.identity_id,
            state_at=deactivation.effective_at + delta,
            known_at=min(
                deactivation.received_at,
                deactivation.recorded_at,
            ) - delta,
        )
        known_deactivation = identity_state_at(
            identity.identity_id,
            state_at=deactivation.effective_at + delta,
            known_at=max(
                deactivation.received_at,
                deactivation.recorded_at,
            ) + delta,
        )
        self.assertEqual(late_deactivation.access_state, Identity.AccessState.ACTIVE)
        self.assertEqual(
            known_deactivation.access_state,
            Identity.AccessState.DEACTIVATED,
        )
        self.assertEqual(known_deactivation.access_epoch, 2)

    def test_terms_guard_failure_rolls_back_every_registration_write(self):
        """W-A1.9 · SELF route: a later registration-record terms-guard failure rolls back the whole act."""
        username = f"p1-rollback-{uuid4().hex}"
        before = self.table_counts(self.registration_tables)
        acceptance_create = PlatformTermsAcceptance.objects.create

        def create_mismatched_acceptance(*args, **kwargs):
            kwargs["subject_reference"] = f"learner-registration:{uuid4()}"
            acceptance = acceptance_create(*args, **kwargs)
            self.assertTrue(
                User.objects.filter(username=username).exists()
            )
            identity = Identity.objects.get(credential__username=username)
            self.assertIsNotNone(self.resolution_for(identity))
            self.assertTrue(
                PlatformTermsAcceptance.objects.filter(pk=acceptance.pk).exists()
            )
            return acceptance

        with patch.object(
            PlatformTermsAcceptance.objects,
            "create",
            side_effect=create_mismatched_acceptance,
        ):
            with self.assertRaises(IntegrityError) as guard_failure:
                self.register(username=username)
        self.assertIn(
            "P1 registration requires acceptance of the latest terms version at its database receipt",
            str(guard_failure.exception),
        )

        self.assertEqual(self.table_counts(self.registration_tables), before)
        self.assertFalse(User.objects.filter(username=username).exists())
        self.assertFalse(
            Identity.objects.filter(credential__username=username).exists()
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM public.core_identityresolution "
                "WHERE canonical_username = %s",
                [canonical_username_v1(username)],
            )
            self.assertEqual(cursor.fetchone()[0], 0)
        self.assertFalse(
            PlatformTermsAcceptance.objects.filter(
                accepted_by_identity__credential__username=username
            ).exists()
        )
        self.assertFalse(
            LearnerRegistration.objects.filter(
                identity__credential__username=username
            ).exists()
        )
        self.assertFalse(
            IdentityTransition.objects.filter(
                identity__credential__username=username
            ).exists()
        )
        self.register(username=username)
