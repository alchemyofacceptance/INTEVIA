from threading import Barrier, Lock, Thread
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth import authenticate
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
    CredentialServiceRefused,
    IdentityLifecycleService,
    InvalidIdentityTransition,
    OriginatingMembershipProvisioningService,
    ProvisioningConflict,
)
from tests.s015_0021_support import (
    create_test_identity,
    register_test_learner,
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
            ("core_eventattendance", "subject_id"),
            ("core_eventattendancetransition", "actor_id"),
            ("core_eventattendanceevidencereference", "supplied_by_id"),
            ("core_eventattendanceeligibilityreceipt", "subject_id"),
            ("core_eventattendanceeligibilityreceipt", "actor_id"),
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
            ("core_eventresourceassertion", "created_by_id"),
            ("core_eventresourcerelationshiptransition", "actor_id"),
            ("core_serviceactivity", "created_by_id"),
            ("core_serviceactivitytransition", "actor_id"),
            ("core_serviceactivityassignment", "assignee_id"),
            ("core_serviceactivityassignment", "assigned_by_id"),
            ("core_serviceworksubmission", "submitted_by_id"),
            ("core_serviceactivityreview", "reviewed_by_id"),
            ("core_serviceactivityevidencereference", "supplied_by_id"),
            ("core_profileeffectproposallineage", "subject_id"),
            ("core_profileeffectproposallineage", "proposer_id"),
            ("core_profileeffectproposaltransition", "actor_id"),
            ("core_profileeffectprojectiondisposition", "actor_id"),
            ("core_course", "created_by_id"),
            ("core_courseversion", "actor_id"),
            ("core_authoritybasis", "issuer_id"),
            ("core_emergencyauthorityenvelope", "beneficiary_id"),
            ("core_governeddetermination", "determiner_id"),
            ("core_governeddetermination", "model_author_id"),
            ("core_governeddetermination", "safeguard_beneficiary_id"),
            ("core_governeddetermination", "tested_actor_id"),
            ("core_livingorganism", "founder_identity_id"),
            ("core_organismcommandreceipt", "actor_id"),
            ("core_organismmembership", "identity_id"),
            ("core_authorityinvalidationevent", "actor_id"),
            ("core_circlestateevent", "actor_id"),
            ("core_contextualroleassignmentstateevent", "actor_id"),
            ("core_coverageassessment", "actor_id"),
            ("core_determinationcontestevent", "actor_id"),
            ("core_livingorganismevent", "actor_id"),
            ("core_membershipconditionstateevent", "actor_id"),
            ("core_obligationstateevent", "actor_id"),
            ("core_organismmembershiptransition", "actor_id"),
            ("core_planningclassificationevent", "actor_id"),
            ("core_restrictedcontinuityevent", "accountable_actor_id"),
            ("core_restrictedcontinuityevent", "actor_id"),
            ("core_visibilitygrantstateevent", "actor_id"),
            ("core_application", "actor_id"),
            ("core_application", "applicant_id"),
            ("core_application", "enterer_id"),
            ("core_applicationdecision", "enterer_id"),
            ("core_applicationdecision", "executor_id"),
            ("core_applicationdecision", "responsible_coordinator_id"),
            ("core_applicationengagement", "actor_id"),
            ("core_applicationengagement", "enterer_id"),
            ("core_applicationwithdrawal", "actor_id"),
            ("core_applicationwithdrawal", "enterer_id"),
            ("core_csaassignment", "actor_id"),
            ("core_csaassignment", "enterer_id"),
            ("core_csaassignmentend", "actor_id"),
            ("core_csaassignmentend", "enterer_id"),
            ("core_applicationlineageevent", "actor_id"),
            ("core_applicationlineageevent", "enterer_identity_id"),
            ("core_applicationlineageevent", "composer_identity_id"),
            ("core_loapplicationquestionversion", "actor_id"),
            ("core_loapplicationquestionversion", "enterer_id"),
            ("core_loapplicationsettingsversion", "actor_id"),
            ("core_loapplicationsettingsversion", "enterer_id"),
            ("core_logovernanceconfigurationversion", "actor_id"),
            ("core_logovernanceconfigurationversion", "enterer_id"),
            ("core_authoritydecision", "actor_id"),
            ("core_officeappointmentconsent", "actor_id"),
            ("core_officeappointmentconsent", "enterer_id"),
            ("core_lofoundingcontentversion", "actor_identity_id"),
            ("core_platformtermsacceptance", "accepted_by_identity_id"),
            ("core_learnerregistration", "identity_id"),
            ("core_livingorganismevent", "enterer_identity_id"),
            ("core_livingorganismevent", "composer_identity_id"),
            ("core_circlestateevent", "enterer_identity_id"),
            ("core_circlestateevent", "composer_identity_id"),
            ("core_organismmembershiptransition", "enterer_identity_id"),
            ("core_organismmembershiptransition", "composer_identity_id"),
            ("core_contextualroleassignmentstateevent", "enterer_identity_id"),
            ("core_contextualroleassignmentstateevent", "composer_identity_id"),
            ("core_membershipconditionstateevent", "enterer_identity_id"),
            ("core_membershipconditionstateevent", "composer_identity_id"),
            ("core_authorityinvalidationevent", "enterer_identity_id"),
            ("core_authorityinvalidationevent", "composer_identity_id"),
            ("core_determinationcontestevent", "enterer_identity_id"),
            ("core_determinationcontestevent", "composer_identity_id"),
            ("core_coverageassessment", "enterer_identity_id"),
            ("core_coverageassessment", "composer_identity_id"),
            ("core_restrictedcontinuityevent", "enterer_identity_id"),
            ("core_restrictedcontinuityevent", "composer_identity_id"),
            ("core_visibilitygrantstateevent", "enterer_identity_id"),
            ("core_visibilitygrantstateevent", "composer_identity_id"),
            ("core_obligationstateevent", "enterer_identity_id"),
            ("core_obligationstateevent", "composer_identity_id"),
            ("core_planningclassificationevent", "enterer_identity_id"),
            ("core_planningclassificationevent", "composer_identity_id"),
            ("core_identityresolution", "identity_id"),
            ("core_identityresolutionsevered", "identity_id"),
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
        self.assertEqual(len(observed), 124)

    def test_database_rejects_invalid_access_state(self):
        credential = User.objects.create_user(username="invalid-state")
        identity = create_test_identity(credential=credential)
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

    @staticmethod
    def registered_learner(username):
        return register_test_learner(
            username=username,
            password="governed-test-password",
            display_name="PostgreSQL registered identity",
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
            return create_test_identity(
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
        identity = create_test_identity(credential=credential)

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
        """W-PC4 concurrent case: one same-epoch password change succeeds exactly once."""
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
        identity.refresh_from_db()
        original_pk = identity.pk
        original_uuid = identity.identity_id
        original_credential_id = identity.credential_id
        original_username = identity.credential.username
        starting_epoch = identity.access_epoch
        user_count_before = User.objects.count()
        passwords = (
            "replacement-one-governed-password",
            "replacement-two-governed-password",
        )

        def change_password(password):
            def operation():
                try:
                    result = CredentialService.change_password(
                        identity_id=identity.identity_id,
                        new_password=password,
                        requesting_actor=identity,
                        session_access_epoch=starting_epoch,
                        correlation_id=uuid4(),
                    )
                except CredentialServiceRefused as error:
                    return password, error
                return password, result

            return operation

        results = self.run_concurrently(
            [change_password(password) for password in passwords]
        )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(isinstance(result, tuple) for result in results), results)
        successes = [
            result for result in results if isinstance(result[1], IdentityTransition)
        ]
        refusals = [
            result
            for result in results
            if isinstance(result[1], CredentialServiceRefused)
        ]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(len(refusals), 1, results)
        self.assertEqual(
            refusals[0][1].code,
            "PASSWORD_CHANGE_SESSION_STALE",
            results,
        )
        identity.refresh_from_db()
        identity.credential.refresh_from_db()
        self.assertEqual(identity.pk, original_pk)
        self.assertEqual(identity.identity_id, original_uuid)
        self.assertEqual(identity.credential_id, original_credential_id)
        self.assertEqual(identity.credential.username, original_username)
        self.assertEqual(identity.access_epoch, starting_epoch + 1, results)
        self.assertEqual(User.objects.count(), user_count_before)
        successful_password = successes[0][0]
        refused_password = refusals[0][0]
        self.assertTrue(identity.credential.check_password(successful_password))
        self.assertFalse(identity.credential.check_password(refused_password))
        self.assertEqual(
            identity.access_transitions.filter(
                action=IdentityTransition.Action.REPLACE_CREDENTIAL
            ).count(),
            1,
        )

    def test_committed_restriction_denies_stale_event_projection(self):
        credential = User.objects.create_user(username="restricted-reader")
        identity = create_test_identity(
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
        identity = self.registered_learner(
            "login-deactivation-race"
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
        identity = self.registered_learner("reactivation-session")
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
            proof_password="governed-test-password",
        )
        self.assertTrue(
            client.get(reverse("shell")).url.startswith(reverse("login"))
        )

    def test_credential_replacement_invalidates_existing_session(self):
        """W-PC3(iii): the operation invalidates the session that made the change."""
        identity = self.registered_learner("replacement-session")
        old_credential = identity.credential
        client = Client()
        client.post(
            reverse("login"),
            {
                "username": "replacement-session",
                "password": "governed-test-password",
            },
        )
        identity.refresh_from_db()
        original_credential_id = identity.credential_id
        original_username = identity.credential.username
        starting_epoch = identity.access_epoch
        transition = CredentialService.change_password(
            identity_id=identity.identity_id,
            new_password="new-governed-test-password",
            requesting_actor=identity,
            session_access_epoch=starting_epoch,
            correlation_id=uuid4(),
        )
        self.assertTrue(
            client.get(reverse("shell")).url.startswith(reverse("login"))
        )
        old_credential.refresh_from_db()
        identity.refresh_from_db()
        self.assertEqual(identity.credential_id, original_credential_id)
        self.assertEqual(identity.credential.username, original_username)
        self.assertEqual(identity.access_epoch, starting_epoch + 1)
        self.assertTrue(old_credential.is_active)
        self.assertTrue(old_credential.check_password("new-governed-test-password"))
        self.assertIsNone(
            authenticate(
                username=original_username,
                password="governed-test-password",
            )
        )
        self.assertEqual(
            authenticate(
                username=original_username,
                password="new-governed-test-password",
            ).pk,
            original_credential_id,
        )
        self.assertEqual(
            transition.action,
            IdentityTransition.Action.REPLACE_CREDENTIAL,
        )

    def test_profilerole_does_not_grant_shell_or_lifecycle_access(self):
        self.assertEqual(ProfileRole.objects.count(), 0)