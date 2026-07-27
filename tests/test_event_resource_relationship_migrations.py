import importlib

from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class EventResourceRelationshipMigrationTests(TransactionTestCase):
    migrate_from = ("core", "0014_s008_event_attendance_foundation")
    migrate_to = ("core", "0015_s011b_event_resource_relationship")
    model_names = (
        "EventResourceRelationship",
        "EventResourceAssertion",
        "EventResourceRelationshipTransition",
        "EventResourceRelationshipEvidence",
    )
    table_names = (
        "core_eventresourcerelationship",
        "core_eventresourceassertion",
        "core_eventresourcerelationshiptransition",
        "core_eventresourcerelationshipevidence",
    )

    @staticmethod
    def migrate(target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.received_leaf_targets = tuple(executor.loader.graph.leaf_nodes())
        old_apps = self.migrate(self.migrate_from)
        User = old_apps.get_model("auth", "User")
        Identity = old_apps.get_model("core", "Identity")
        Event = old_apps.get_model("core", "Event")
        EventRegistration = old_apps.get_model("core", "EventRegistration")
        EventAttendance = old_apps.get_model("core", "EventAttendance")
        LibraryResource = old_apps.get_model("core", "LibraryResource")
        LibraryResourceVersion = old_apps.get_model("core", "LibraryResourceVersion")

        credential = User.objects.create(username="s011b-migration")
        identity = Identity.objects.create(
            credential=credential,
            identity_id="00000000-0000-4000-8000-00000000011b",
            canonical_username="s011b-migration",
            access_state="active",
            access_epoch=4,
        )
        occurred_at = timezone.now()
        event = Event.objects.create(
            event_id="event.s011b.migration",
            title="S011-B migration",
            description="Preexisting Event",
            owner=identity,
            state="active",
            created_at=occurred_at,
        )
        registration = EventRegistration.objects.create(
            registration_id="registration.s011b.migration",
            event=event,
            participant=identity,
            state="registered",
            origin="self",
            event_state_at_registration="active",
            eligibility_basis_type="other",
            eligibility_basis_reference="eligibility.s011b.migration",
            eligibility_evaluated_at=occurred_at,
            registered_at=occurred_at,
        )
        attendance = EventAttendance.objects.create(
            attendance_id="attendance.s011b.migration",
            event=event,
            subject=identity,
            status="present",
            observed_at=occurred_at,
            origin="registered",
            supporting_registration=registration,
        )
        resource = LibraryResource.objects.create(
            resource_id="library.s011b.migration",
            created_by=identity,
            state="published",
            created_at=occurred_at,
        )
        version = LibraryResourceVersion.objects.create(
            resource=resource,
            version_number=1,
            content="Preexisting exact version",
            created_by=identity,
            created_at=occurred_at,
        )
        self.rows = {
            "Identity": identity.pk,
            "Event": event.pk,
            "EventRegistration": registration.pk,
            "EventAttendance": attendance.pk,
            "LibraryResource": resource.pk,
            "LibraryResourceVersion": version.pk,
        }

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.received_leaf_targets)
        for table_name in self.table_names:
            self.assertIn(table_name, connection.introspection.table_names())
        super().tearDown()

    def assert_preexisting_rows(self, apps):
        for model_name, primary_key in self.rows.items():
            with self.subTest(model=model_name):
                self.assertTrue(
                    apps.get_model("core", model_name).objects.filter(
                        pk=primary_key
                    ).exists()
                )

    def test_exact_dependency_ancestry_and_schema_only_operations(self):
        migration_module = importlib.import_module(
            "core.migrations.0015_s011b_event_resource_relationship"
        )
        self.assertEqual(migration_module.Migration.dependencies, [self.migrate_from])
        self.assertEqual(
            [operation.name for operation in migration_module.Migration.operations if isinstance(operation, migrations.CreateModel)],
            list(self.model_names),
        )
        self.assertFalse(
            any(
                isinstance(operation, migrations.RunPython)
                for operation in migration_module.Migration.operations
            )
        )
        executor = MigrationExecutor(connection)
        self.assertIn(self.migrate_to, executor.loader.graph.nodes)
        ancestry = executor.loader.graph.forwards_plan(self.migrate_to)
        self.assertIn(self.migrate_from, ancestry)
        self.assertEqual(ancestry[-1], self.migrate_to)

    def test_forward_reverse_and_reapply_preserve_preexisting_data(self):
        new_apps = self.migrate(self.migrate_to)
        self.assert_preexisting_rows(new_apps)
        for model_name, table_name in zip(self.model_names, self.table_names, strict=True):
            with self.subTest(forward_model=model_name):
                self.assertEqual(new_apps.get_model("core", model_name).objects.count(), 0)
                self.assertIn(table_name, connection.introspection.table_names())

        old_apps = self.migrate(self.migrate_from)
        self.assert_preexisting_rows(old_apps)
        tables_after_reverse = connection.introspection.table_names()
        for table_name in self.table_names:
            self.assertNotIn(table_name, tables_after_reverse)
        for preserved_table in (
            "core_identity",
            "core_event",
            "core_eventregistration",
            "core_eventattendance",
            "core_libraryresource",
            "core_libraryresourceversion",
        ):
            self.assertIn(preserved_table, tables_after_reverse)

        reapplied_apps = self.migrate(self.migrate_to)
        self.assert_preexisting_rows(reapplied_apps)
        for model_name, table_name in zip(self.model_names, self.table_names, strict=True):
            with self.subTest(reapplied_model=model_name):
                self.assertEqual(reapplied_apps.get_model("core", model_name).objects.count(), 0)
                self.assertIn(table_name, connection.introspection.table_names())
