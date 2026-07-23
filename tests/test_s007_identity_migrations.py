from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class IdentityMigrationRehearsalTests(TransactionTestCase):
    migrate_from = ("core", "0010_event_registration_foundation")
    migrate_to = ("core", "0013_s007_identity_constraints")

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.received_leaf_targets = tuple(executor.loader.graph.leaf_nodes())
        old_apps = self.migrate(self.migrate_from)
        User = old_apps.get_model("auth", "User")
        Profile = old_apps.get_model("core", "Profile")
        Role = old_apps.get_model("core", "Role")
        ProfileRole = old_apps.get_model("core", "ProfileRole")
        Contribution = old_apps.get_model("core", "Contribution")
        Event = old_apps.get_model("core", "Event")
        EventParticipation = old_apps.get_model("core", "EventParticipation")
        EventRegistration = old_apps.get_model("core", "EventRegistration")
        EventRegistrationTransition = old_apps.get_model(
            "core", "EventRegistrationTransition"
        )
        LibraryResource = old_apps.get_model("core", "LibraryResource")
        Service = old_apps.get_model("core", "Service")
        CareResponse = old_apps.get_model("core", "CareResponse")

        credential = User.objects.create(
            username="MigrationHuman",
            password="unusable-historical-placeholder",
        )
        profile = Profile.objects.create(
            user=credential,
            display_name="Migration Human",
        )
        role = Role.objects.create(name="Migration compatibility role")
        profile_role = ProfileRole.objects.create(profile=profile, role=role)
        occurred_at = timezone.now()
        contribution = Contribution.objects.create(
            contribution_id="migration:contribution",
            contributor=profile,
        )
        event = Event.objects.create(
            event_id="migration:event",
            title="Migration Event",
            owner=profile,
            state="published",
            created_at=occurred_at,
        )
        participation = EventParticipation.objects.create(
            event=event,
            participant=profile,
            attached_by=profile,
            authority_reference="migration:authority",
            attached_at=occurred_at,
        )
        registration = EventRegistration.objects.create(
            registration_id="migration:registration",
            event=event,
            participant=profile,
            state="registered",
            origin="self",
            event_state_at_registration="published",
            eligibility_basis_type="other",
            eligibility_basis_reference="migration:eligibility",
            eligibility_evaluated_at=occurred_at,
            registered_at=occurred_at,
        )
        registration_transition = EventRegistrationTransition.objects.create(
            registration=registration,
            from_state="new",
            to_state="registered",
            action_type="register_self",
            actor=profile,
            authority_reference="migration:authority",
            authority_event=event,
            authority_participant=profile,
            authority_evaluated_at=occurred_at,
            cancellation_basis="",
            basis_source="",
            occurred_at=occurred_at,
            lineage_reference="migration:registration:transition",
        )
        library_resource = LibraryResource.objects.create(
            resource_id="migration:library",
            created_by=profile,
            created_at=occurred_at,
        )
        service = Service.objects.create(
            service_id="migration:service",
            created_by=profile,
            created_at=occurred_at,
        )
        care_response = CareResponse.objects.create(
            response_id="migration:care",
            trigger_type="human_request",
            trigger_reference="migration:trigger",
            context_source="human_declared",
            context_reference="migration:context",
            output_type="orientation_guidance",
            content="Migration guidance",
            actor=profile,
            authority_reference="migration:authority",
            evidence_reference="migration:evidence",
            occurred_at=occurred_at,
            lineage_reference="migration:care:lineage",
        )
        self.profile_pk = profile.pk
        self.credential_pk = credential.pk
        self.profile_role_pk = profile_role.pk
        self.attribution_rows = {
            "Contribution": (contribution.pk, ("contributor_id",)),
            "Event": (event.pk, ("owner_id",)),
            "EventParticipation": (
                participation.pk,
                ("participant_id", "attached_by_id"),
            ),
            "EventRegistration": (registration.pk, ("participant_id",)),
            "EventRegistrationTransition": (
                registration_transition.pk,
                ("actor_id", "authority_participant_id"),
            ),
            "LibraryResource": (library_resource.pk, ("created_by_id",)),
            "Service": (service.pk, ("created_by_id",)),
            "CareResponse": (care_response.pk, ("actor_id",)),
        }

    def assert_attribution(self, apps):
        for model_name, (row_pk, field_names) in self.attribution_rows.items():
            row = apps.get_model("core", model_name).objects.get(pk=row_pk)
            for field_name in field_names:
                self.assertEqual(getattr(row, field_name), self.profile_pk)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.received_leaf_targets)
        self.assertIn(
            "core_eventattendance",
            connection.introspection.table_names(),
        )
        super().tearDown()

    def test_populated_rename_rollback_and_reapply_preserve_lineage(self):
        new_apps = self.migrate(self.migrate_to)
        Identity = new_apps.get_model("core", "Identity")
        ProfileRole = new_apps.get_model("core", "ProfileRole")
        ContentType = new_apps.get_model("contenttypes", "ContentType")
        Permission = new_apps.get_model("auth", "Permission")

        identity = Identity.objects.get(pk=self.profile_pk)
        self.assertEqual(identity.credential_id, self.credential_pk)
        self.assertEqual(identity.display_name, "Migration Human")
        self.assertEqual(identity.canonical_username, "migrationhuman")
        self.assertEqual(identity.access_state, "pending")
        self.assertIsNotNone(identity.identity_id)
        self.assertEqual(
            ProfileRole.objects.get(pk=self.profile_role_pk).identity_id,
            identity.pk,
        )
        self.assert_attribution(new_apps)
        self.assertFalse(
            ContentType.objects.filter(app_label="core", model="profile").exists()
        )
        identity_content_type = ContentType.objects.get(
            app_label="core",
            model="identity",
        )
        self.assertEqual(
            set(
                Permission.objects.filter(
                    content_type=identity_content_type,
                    codename__endswith="_identity",
                ).values_list("codename", flat=True)
            ),
            {
                "add_identity",
                "change_identity",
                "delete_identity",
                "view_identity",
            },
        )

        old_apps = self.migrate(self.migrate_from)
        Profile = old_apps.get_model("core", "Profile")
        OldProfileRole = old_apps.get_model("core", "ProfileRole")
        OldContentType = old_apps.get_model("contenttypes", "ContentType")
        self.assertEqual(
            Profile.objects.get(pk=self.profile_pk).user_id,
            self.credential_pk,
        )
        self.assertEqual(
            OldProfileRole.objects.get(pk=self.profile_role_pk).profile_id,
            self.profile_pk,
        )
        self.assert_attribution(old_apps)
        self.assertTrue(
            OldContentType.objects.filter(
                app_label="core",
                model="profile",
            ).exists()
        )
        self.assertFalse(
            OldContentType.objects.filter(
                app_label="core",
                model="identity",
            ).exists()
        )

        reapplied_apps = self.migrate(self.migrate_to)
        ReappliedIdentity = reapplied_apps.get_model("core", "Identity")
        reapplied = ReappliedIdentity.objects.get(pk=self.profile_pk)
        self.assertEqual(reapplied.credential_id, self.credential_pk)
        self.assertEqual(reapplied.canonical_username, "migrationhuman")
        self.assert_attribution(reapplied_apps)