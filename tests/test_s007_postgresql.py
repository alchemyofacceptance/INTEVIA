from threading import Barrier, Lock, Thread
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Event,
    Identity,
    IdentityTransition,
    OriginatingMembershipProvisioningRequest,
    ProfileRole,
    ProvisioningReconciliationAttempt,
)
from src.intevia.services.event_read_service import EventNotVisible, EventReadService
from src.intevia.services.identity_service import (
    CredentialService,
    IdentityLifecycleService,
    InvalidIdentityTransition,
    OriginatingMembershipProvisioningService,
    ProvisioningConflict,
)


POSTGRESQL_ONLY = skipUnless(
    connection.vendor == "postgresql",
    "PostgreSQL S007 qualification guardian",
)


@POSTGRESQL_ONLY
class S007PostgreSQLCatalogueTests(TransactionTestCase):
    reset_sequences = True

    def test_catalogue_identity_constraints_and_permissions(self):
        cursor = connection.cursor()
        self.addCleanup(cursor.close)
        cursor.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE connamespace = 'public'::regnamespace
                    AND conrelid IN (
                        'core_identity'::regclass,
                        'core_identitytransition'::regclass,
                        'core_originatingmembershipprovisioningrequest'::regclass,
                        'core_provisioningreconciliationattempt'::regclass
                    )
                """
        )
        constraint_names = {row[0] for row in cursor.fetchall()}
        cursor.execute(
                """
                SELECT confdeltype
                FROM pg_constraint
                WHERE conrelid = 'core_identity'::regclass
                    AND confrelid = 'auth_user'::regclass
                    AND contype = 'f'
                """
        )
        credential_delete_action = cursor.fetchone()[0]
        cursor.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                    AND tablename IN (
                        'core_identity',
                        'core_originatingmembershipprovisioningrequest'
                    )
                """
        )
        index_names = {row[0] for row in cursor.fetchall()}
        cursor.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                    AND tablename IN ('core_profile', 'core_identity')
                """
        )
        identity_tables = {row[0] for row in cursor.fetchall()}

        expected_constraints = {
            "identity_access_epoch_nonnegative",
            "identity_access_state_valid",
            "identity_transition_prior_required",
            "identity_correction_action_required",
            "originating_contract_version_positive",
            "originating_request_not_self_superseding",
            "provision_correction_state_required",
            "provision_attempt_not_self_previous",
        }
        self.assertEqual(expected_constraints - constraint_names, set())
        self.assertIn("one_active_originating_membership_intent", index_names)
        self.assertEqual(credential_delete_action, "a")
        self.assertEqual(identity_tables, {"core_identity"})

        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType

        self.assertFalse(
            ContentType.objects.filter(app_label="core", model="profile").exists()
        )
        content_type = ContentType.objects.get(
            app_label="core", model="identity"
        )
        self.assertEqual(
            set(
                Permission.objects.filter(content_type=content_type).values_list(
                    "codename", flat=True
                )
            ),
            {
                "add_identity",
                "change_identity",
                "delete_identity",
                "view_identity",
            },
        )
        self.assertFalse(
            Permission.objects.filter(codename__endswith="_profile").exists()
        )

    def test_all_historical_identity_foreign_keys_target_renamed_table(self):
        expected = {
            ("core_profilerole", "identity_id"),
            ("core_contribution", "contributor_id"),
            ("core_contributionversion", "created_by_id"),
            ("core_contributiontransition", "actor_id"),
            ("core_contributiondecision", "decision_actor_id"),
            ("core_evidencereference", "added_by_id"),
            ("core_event", "owner_id"),
            ("core_eventtransition", "actor_id"),
            ("core_eventparticipation", "participant_id"),
            ("core_eventparticipation", "attached_by_id"),
            ("core_eventevidencereference", "supplied_by_id"),
            ("core_eventregistration", "participant_id"),
            ("core_eventregistrationtransition", "actor_id"),
            ("core_eventregistrationtransition", "authority_participant_id"),
            ("core_eventregistrationevidencereference", "supplied_by_id"),
            ("core_libraryresource", "created_by_id"),
            ("core_libraryresourceversion", "created_by_id"),
            ("core_libraryresourcetransition", "actor_id"),
            ("core_libraryresourceevidencereference", "supplied_by_id"),
            ("core_service", "created_by_id"),
            ("core_serviceversion", "created_by_id"),
            ("core_servicetransition", "actor_id"),
            ("core_serviceevidencereference", "supplied_by_id"),
            ("core_libraryserviceassociation", "actor_id"),
            ("core_serviceeventassociation", "actor_id"),
            ("core_servicedeliveryevidencereference", "supplied_by_id"),
            ("core_careresponse", "actor_id"),
        }
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source.relname, attribute.attname
                FROM pg_constraint constraint_row
                JOIN pg_class source
                  ON source.oid = constraint_row.conrelid
                JOIN pg_class target
                  ON target.oid = constraint_row.confrelid
                JOIN LATERAL unnest(constraint_row.conkey) AS key(attnum)
                  ON TRUE
                JOIN pg_attribute attribute
                  ON attribute.attrelid = source.oid
                 AND attribute.attnum = key.attnum
                WHERE constraint_row.contype = 'f'
                  AND target.relname = 'core_identity'
                  AND source.relname NOT IN (
                    'core_identitytransition',
                    'core_originatingmembershipprovisioningrequest'
                  )
                """
            )
            observed = set(cursor.fetchall())
        self.assertEqual(observed, expected)
        self.assertEqual(len(observed), 27)

    def test_database_rejects_invalid_access_state(self):
        credential = User.objects.create_user(username="invalid-state")
        identity = Identity.objects.create(credential=credential)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Identity.objects.filter(pk=identity.pk).update(
                    access_state="not-a-state"
                )


@POSTGRESQL_ONLY
class S007PostgreSQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    @staticmethod
    def run_concurrently(functions):
        barrier = Barrier(len(functions))
        result_lock = Lock()
        results = []

        def invoke(function):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                value = function()
            except Exception as error:
                value = error
            finally:
                close_old_connections()
            with result_lock:
                results.append(value)

        threads = [Thread(target=invoke, args=(function,)) for function in functions]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        if any(thread.is_alive() for thread in threads):
            raise AssertionError("concurrent guardian did not complete")
        return results

    @staticmethod
    def provision(username="postgres-identity"):
        return CredentialService.provision(
            username=username,
            password="governed-test-password",
            display_name="PostgreSQL Identity",
            human_classification="HUMAN DEVELOPMENT IDENTITY",
            authority_reference="authority:provision",
            evidence_reference="evidence:provision",
            correlation_id=uuid4(),
            technical_executor="postgres-guardian",
        )

    @staticmethod
    def fulfil(request, evidence="evidence:membership-fulfilled"):
        return OriginatingMembershipProvisioningService.reconcile(
            request_id=request.pk,
            state=ProvisioningReconciliationAttempt.State.FULFILLED,
            authority_reference="authority:fulfil",
            evidence_reference=evidence,
            correlation_id=uuid4(),
        )

    def test_concurrent_canonical_username_has_one_complete_winner(self):
        def provision(username):
            return lambda: CredentialService.provision(
                username=username,
                password="governed-test-password",
                display_name="Concurrent Identity",
                human_classification="HUMAN DEVELOPMENT IDENTITY",
                authority_reference="authority:concurrent",
                evidence_reference="evidence:concurrent",
                correlation_id=uuid4(),
                technical_executor="postgres-guardian",
            )

        results = self.run_concurrently(
            [provision("ConcurrentName"), provision("concurrentname")]
        )
        self.assertEqual(sum(isinstance(result, tuple) for result in results), 1)
        self.assertEqual(sum(isinstance(result, Exception) for result in results), 1)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Identity.objects.count(), 1)
        self.assertEqual(
            Identity.objects.get().canonical_username,
            "concurrentname",
        )
        self.assertEqual(OriginatingMembershipProvisioningRequest.objects.count(), 1)
        self.assertEqual(IdentityTransition.objects.count(), 1)

    def test_concurrent_identity_binding_has_one_winner(self):
        credential = User.objects.create_user(username="single-credential")

        def bind():
            return Identity.objects.create(
                credential_id=credential.pk,
                display_name="Bound Identity",
                canonical_username="single-credential",
            )

        results = self.run_concurrently([bind, bind])
        self.assertEqual(sum(isinstance(result, Identity) for result in results), 1)
        self.assertEqual(sum(isinstance(result, Exception) for result in results), 1)
        self.assertEqual(Identity.objects.count(), 1)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(IdentityTransition.objects.count(), 0)
        self.assertEqual(OriginatingMembershipProvisioningRequest.objects.count(), 0)

    def test_concurrent_activation_appends_once(self):
        identity, request = self.provision("activation-race")
        self.fulfil(request)

        def activate():
            return IdentityLifecycleService.activate(
                identity_id=identity.identity_id,
                fulfilment_evidence_reference="evidence:membership-fulfilled",
                requesting_actor=None,
                authority_reference="authority:activate",
                technical_executor="postgres-guardian",
                correlation_id=uuid4(),
            )

        results = self.run_concurrently([activate, activate])
        self.assertEqual(
            sum(isinstance(result, IdentityTransition) for result in results), 1
        )
        self.assertEqual(
            sum(isinstance(result, InvalidIdentityTransition) for result in results),
            1,
        )
        identity.refresh_from_db()
        self.assertEqual(identity.access_state, Identity.AccessState.ACTIVE)
        self.assertEqual(identity.access_epoch, 0)
        self.assertEqual(
            identity.access_transitions.filter(
                action=IdentityTransition.Action.ACTIVATE
            ).count(),
            1,
        )

    def test_concurrent_provisioning_intent_and_supersession(self):
        credential = User.objects.create_user(username="provisioning-race")
        identity = Identity.objects.create(credential=credential)

        def request():
            return OriginatingMembershipProvisioningService.request(
                identity=identity,
                authority_reference="authority:intent",
                evidence_reference="evidence:intent",
                correlation_id=uuid4(),
            )

        results = self.run_concurrently([request, request])
        self.assertEqual(
            sum(
                isinstance(result, OriginatingMembershipProvisioningRequest)
                for result in results
            ),
            1,
        )
        self.assertEqual(
            sum(isinstance(result, ProvisioningConflict) for result in results), 1
        )
        first = OriginatingMembershipProvisioningRequest.objects.get()
        replacement = OriginatingMembershipProvisioningService.request(
            identity=identity,
            authority_reference="authority:supersede",
            evidence_reference="evidence:supersede",
            correlation_id=uuid4(),
            supersedes=first,
        )
        first.refresh_from_db()
        self.assertIsNotNone(first.superseded_at)
        self.assertIsNone(replacement.superseded_at)
        self.assertEqual(
            OriginatingMembershipProvisioningRequest.objects.filter(
                superseded_at__isnull=True
            ).count(),
            1,
        )

    def test_concurrent_credential_replacements_leave_one_usable_binding(self):
        identity, request = self.provision("replacement-race")
        self.fulfil(request)
        IdentityLifecycleService.activate(
            identity_id=identity.identity_id,
            fulfilment_evidence_reference="evidence:membership-fulfilled",
            requesting_actor=None,
            authority_reference="authority:activate",
            technical_executor="postgres-guardian",
            correlation_id=uuid4(),
        )
        original_pk = identity.pk
        original_uuid = identity.identity_id

        def replace(username):
            return lambda: CredentialService.replace(
                identity_id=identity.identity_id,
                replacement_username=username,
                replacement_password="replacement-governed-password",
                requesting_actor=None,
                authority_reference="authority:replace",
                evidence_reference="evidence:replace",
                correlation_id=uuid4(),
                technical_executor="postgres-guardian",
            )

        results = self.run_concurrently(
            [replace("replacement-one"), replace("replacement-two")]
        )
        accepted = sum(
            isinstance(result, IdentityTransition) for result in results
        )
        self.assertIn(accepted, {1, 2}, results)
        identity.refresh_from_db()
        self.assertEqual(identity.pk, original_pk)
        self.assertEqual(identity.identity_id, original_uuid)
        self.assertEqual(identity.access_epoch, accepted, results)
        self.assertEqual(User.objects.filter(is_active=True).count(), 1)
        self.assertEqual(User.objects.count(), accepted + 1, results)
        self.assertEqual(identity.credential_id, User.objects.get(is_active=True).pk)
        for retired in User.objects.exclude(pk=identity.credential_id):
            self.assertFalse(retired.has_usable_password())
        self.assertEqual(
            identity.access_transitions.filter(
                action=IdentityTransition.Action.REPLACE_CREDENTIAL
            ).count(),
            accepted,
        )

    def test_committed_restriction_denies_stale_event_projection(self):
        credential = User.objects.create_user(username="restricted-reader")
        identity = Identity.objects.create(
            credential=credential,
            access_state=Identity.AccessState.ACTIVE,
        )
        Event.objects.create(
            event_id="event:restriction-race",
            title="Restriction Race",
            owner=identity,
            state=Event.State.PUBLISHED,
            created_at=timezone.now(),
        )
        stale_identity = Identity.objects.get(pk=identity.pk)
        IdentityLifecycleService.change_access(
            identity_id=identity.identity_id,
            action=IdentityTransition.Action.RESTRICT,
            requesting_actor=None,
            authority_reference="authority:restrict",
            technical_executor="postgres-guardian",
            evidence_reference="evidence:restrict",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(),
        )
        with self.assertRaises(EventNotVisible):
            EventReadService.list_visible(stale_identity)

    def test_login_racing_deactivation_has_no_usable_product_session(self):
        identity, request = self.provision("login-deactivation-race")
        self.fulfil(request)
        IdentityLifecycleService.activate(
            identity_id=identity.identity_id,
            fulfilment_evidence_reference="evidence:membership-fulfilled",
            requesting_actor=None,
            authority_reference="authority:activate",
            technical_executor="postgres-guardian",
            correlation_id=uuid4(),
        )
        client = Client()

        def login():
            return client.post(
                reverse("login"),
                {
                    "username": "login-deactivation-race",
                    "password": "governed-test-password",
                },
            )

        def deactivate():
            return IdentityLifecycleService.change_access(
                identity_id=identity.identity_id,
                action=IdentityTransition.Action.DEACTIVATE,
                requesting_actor=None,
                authority_reference="authority:deactivate",
                technical_executor="postgres-guardian",
                evidence_reference="evidence:deactivate",
                reason_category=IdentityTransition.ReasonCategory.SECURITY,
                correlation_id=uuid4(),
            )

        self.run_concurrently([login, deactivate])
        identity.refresh_from_db()
        identity.credential.refresh_from_db()
        self.assertEqual(identity.access_state, Identity.AccessState.DEACTIVATED)
        self.assertFalse(identity.credential.is_active)
        self.assertTrue(
            client.get(reverse("shell")).url.startswith(reverse("login"))
        )
        self.assertTrue(
            client.get(reverse("event-list")).url.startswith(reverse("login"))
        )

    def test_reactivation_does_not_revive_flushed_session(self):
        identity, request = self.provision("reactivation-session")
        self.fulfil(request)
        IdentityLifecycleService.activate(
            identity_id=identity.identity_id,
            fulfilment_evidence_reference="evidence:membership-fulfilled",
            requesting_actor=None,
            authority_reference="authority:activate",
            technical_executor="postgres-guardian",
            correlation_id=uuid4(),
        )
        client = Client()
        client.post(
            reverse("login"),
            {
                "username": "reactivation-session",
                "password": "governed-test-password",
            },
        )
        IdentityLifecycleService.change_access(
            identity_id=identity.identity_id,
            action=IdentityTransition.Action.DEACTIVATE,
            requesting_actor=None,
            authority_reference="authority:deactivate",
            technical_executor="postgres-guardian",
            evidence_reference="evidence:deactivate",
            reason_category=IdentityTransition.ReasonCategory.SECURITY,
            correlation_id=uuid4(),
        )
        self.assertTrue(
            client.get(reverse("shell")).url.startswith(reverse("login"))
        )
        IdentityLifecycleService.change_access(
            identity_id=identity.identity_id,
            action=IdentityTransition.Action.REACTIVATE,
            requesting_actor=None,
            authority_reference="authority:reactivate",
            technical_executor="postgres-guardian",
            evidence_reference="evidence:membership-fulfilled",
            reason_category=IdentityTransition.ReasonCategory.ACCESS_REVIEW,
            correlation_id=uuid4(),
        )
        self.assertTrue(
            client.get(reverse("shell")).url.startswith(reverse("login"))
        )

    def test_credential_replacement_invalidates_existing_session(self):
        identity, request = self.provision("replacement-session")
        self.fulfil(request)
        IdentityLifecycleService.activate(
            identity_id=identity.identity_id,
            fulfilment_evidence_reference="evidence:membership-fulfilled",
            requesting_actor=None,
            authority_reference="authority:activate",
            technical_executor="postgres-guardian",
            correlation_id=uuid4(),
        )
        old_credential = identity.credential
        client = Client()
        client.post(
            reverse("login"),
            {
                "username": "replacement-session",
                "password": "governed-test-password",
            },
        )
        CredentialService.replace(
            identity_id=identity.identity_id,
            replacement_username="replacement-session-new",
            replacement_password="new-governed-test-password",
            requesting_actor=None,
            authority_reference="authority:replace",
            evidence_reference="evidence:replace",
            correlation_id=uuid4(),
            technical_executor="postgres-guardian",
        )
        self.assertTrue(
            client.get(reverse("shell")).url.startswith(reverse("login"))
        )
        old_credential.refresh_from_db()
        self.assertFalse(old_credential.is_active)
        self.assertFalse(old_credential.has_usable_password())

    def test_profilerole_does_not_grant_shell_or_lifecycle_access(self):
        self.assertEqual(ProfileRole.objects.count(), 0)